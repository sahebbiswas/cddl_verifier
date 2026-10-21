"""Semantic analysis and name resolution for a parsed CDDL schema (#70).

:func:`resolve` takes the ``Schema`` the parser produced and either returns a
:class:`ResolvedSchema` or raises :class:`CDDLSemanticError` with the position
of the first problem. It checks what the grammar cannot:

* every name is defined, by the schema or by the RFC 8610 prelude, or is a
  generic parameter in scope, or is a socket (``$x`` / ``$$x``, which may be
  empty)
* a name is defined once with ``=``; ``/=`` extends a type and ``//=`` a group,
  and ``$x`` is a type socket while ``$$x`` is a group socket
* a rule is a type or a group, decided for rules the parser could not
  classify (``a = b``, ``a = (b)``), and each name is used as what it is:
  a group cannot stand where a type is expected, ``~x`` needs a map, array
  or tag, ``&x`` a group, and a map entry without a key must be a group
* generic references give as many arguments as the rule has parameters
* control operators are known (RFC 8610, RFC 9165 and RFC 9741) and are
  applied to a target and argument of a fitting kind, when that kind is
  evident (``tstr .regexp 1`` and ``map .size 2`` are rejected)
* ranges are between two numbers of the same kind and are not empty, and
  occurrences ``n*m`` have ``n <= m``
* no rule is defined only in terms of itself (``a = b``, ``b = a``)

Modules are linked before resolution (``modules.py``, #121): the rules of a
module the schema imports in the comment syntax of the CDDL modules draft
(draft-ietf-cbor-cddl-modules), ``;# import rfc9393 as coswid``, are part of
the schema, named ``coswid.<rule>``. Names from a module that could not be
found are not checked: a schema may use ``coswid.anything`` after an import
with a prefix, or any undefined name after an unprefixed ``;# import name`` or
``;# include name``. Such names are listed in ``ResolvedSchema.external`` and
stand for unknown types.

The resolved model maps every name to a :class:`RuleDef` (user rules first,
then the prelude) and instantiates generics on demand, with memoization:
``resolved.instantiate("pair", (uint, tstr))`` gives the rule's type with the
parameters replaced. Nothing is expanded eagerly, so recursive rules are fine.

See ``docs/CDDL_AST_DESIGN.md`` §8.
"""

import difflib
import re
from dataclasses import fields, replace
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple, Union

from .ast import (AnyItem, Array, BytesLit, ChoiceFrom, Control, FloatLit, Group,
                  GroupChoice, GroupRef, GroupRule, InlineGroup, IntLit, Major, Map,
                  Member, Name, Occurrence, Paren, Range, Schema, Span, Tag, TextLit,
                  Type, TypeRule, Unwrap, walk)
from . import abnf, computed, conversions, xsd_regex
from .errors import CDDLSemanticError
from .prelude import parse_prelude

#: Control operators from RFC 8610 §3.8, RFC 9165 and RFC 9741.
KNOWN_CONTROLS = frozenset({
    # RFC 8610
    'size', 'bits', 'regexp', 'cbor', 'cborseq', 'within', 'and',
    'lt', 'le', 'gt', 'ge', 'eq', 'ne', 'default',
    # RFC 9165
    'plus', 'cat', 'det', 'abnf', 'abnfb', 'feature',
    # RFC 9741
    'b64u', 'b64c', 'b64u-sloppy', 'b64c-sloppy', 'b45', 'b32', 'h32',
    'hex', 'hexlc', 'hexuc', 'base10', 'printf', 'json', 'join',
})

# Kinds of data item a type can describe, used to check control operators.
_NUMERIC = frozenset({'uint', 'nint', 'float'})

#: How many names :meth:`_Resolver.kinds` follows before giving up, so a long
#: alias chain cannot exhaust the Python stack.
_MAX_ALIAS_DEPTH = 64

#: What a control's target may be, and what its argument may be. A kind set
#: is only checked when every alternative of the target or argument has a
#: known kind; ``None`` means anything goes.
_CONTROL_KINDS: Dict[str, Tuple[Optional[FrozenSet[str]], Optional[FrozenSet[str]]]] = {
    'size': (frozenset({'uint', 'tstr', 'bstr'}), frozenset({'uint'})),
    'bits': (frozenset({'uint', 'bstr'}), frozenset({'uint'})),
    'regexp': (frozenset({'tstr'}), frozenset({'tstr'})),
    'cbor': (frozenset({'bstr'}), None),
    'cborseq': (frozenset({'bstr'}), frozenset({'array'})),
    'lt': (_NUMERIC, _NUMERIC),
    'le': (_NUMERIC, _NUMERIC),
    'gt': (_NUMERIC, _NUMERIC),
    'ge': (_NUMERIC, _NUMERIC),
    # RFC 9165: both ABNF controls apply to either kind of string; the ABNF is text
    'abnf': (frozenset({'tstr', 'bstr'}), frozenset({'tstr'})),
    'abnfb': (frozenset({'tstr', 'bstr'}), frozenset({'tstr'})),
    'plus': (_NUMERIC, _NUMERIC),
    'cat': (frozenset({'tstr', 'bstr'}), frozenset({'tstr', 'bstr'})),
    'det': (frozenset({'tstr', 'bstr'}), frozenset({'tstr', 'bstr'})),
    # RFC 9741: text conversions
    **{op: (frozenset({'tstr'}), frozenset({'bstr'}))
       for op in ('b64u', 'b64c', 'b64u-sloppy', 'b64c-sloppy', 'b45', 'b32', 'h32',
                  'hex', 'hexlc', 'hexuc')},
    'base10': (frozenset({'tstr'}), frozenset({'uint', 'nint'})),
    'printf': (frozenset({'tstr'}), frozenset({'array'})),
    'json': (frozenset({'tstr'}), None),
    'join': (frozenset({'tstr', 'bstr'}), frozenset({'array'})),
}

_KIND_NAMES = {
    'uint': 'an unsigned integer', 'nint': 'a negative integer', 'float': 'a float',
    'tstr': 'a text string', 'bstr': 'a byte string', 'array': 'an array',
    'map': 'a map', 'tag': 'a tagged item', 'bool': 'a boolean', 'nil': 'null',
    'undefined': 'undefined', 'simple': 'a simple value',
}

#: Controls whose argument is checked as the schema is read (and again once a generic
#: rule's arguments are in).
#: ``.feature`` has no entry in ``_CONTROL_KINDS`` because it applies to any target; its
#: controller (a name or ``[name, detail]``) is checked by ``computed.feature``.
_ARGUMENT_CHECKED = frozenset({'bits', 'regexp', 'abnf', 'abnfb', 'plus', 'cat', 'det',
                               'printf', 'join', 'feature'})

TYPE, GROUP = 'type', 'group'


def _group_hint(name: str, control: Optional[str]) -> str:
    """How to use the group *name* where a type is needed, given the control it follows.

    A wrapper makes a map or an array and ``&name`` an enumeration of the group's
    values, so only the forms the control's argument can take are suggested (#224).
    """
    wrap = f"wrap it as '{{ {name} }}' or '[ {name} ]'"
    if control is None:
        return wrap
    if control == 'bits':       # '.bits' takes an enumeration (RFC 8610 3.8.5)
        return f"use '&{name}' for the bit positions it lists"
    argument = _CONTROL_KINDS.get(control, (None, None))[1]
    if argument is None:
        return f"{wrap}, or use '&{name}' for its values"
    if argument == frozenset({'array'}):
        return f"wrap it as '[ {name} ]'"
    return f"use '&{name}' for its values"


class RuleDef:
    """Everything the schema says about one name.

    Attributes:
        name:       The rule name, including any ``$`` or ``$$``.
        kind:       ``"type"`` or ``"group"``.
        params:     Generic parameter names (``()`` if not generic).
        rules:      The defining rules in source order: the ``=`` rule, if
                    any, first, then each ``/=`` or ``//=`` extension.
        prelude:    True for rules from the RFC 8610 prelude.
    """

    def __init__(self, name: str, kind: str, params: Tuple[str, ...], rules: List,
                 prelude: bool = False):
        self.name = name
        self.kind = kind
        self.params = params
        self.rules = rules
        self.prelude = prelude

    @property
    def socket(self) -> bool:
        return self.name.startswith('$')

    @property
    def span(self) -> Span:
        return self.rules[0].span if self.rules else Span(0, 0)

    def type(self) -> Type:
        """The rule's type: the alternatives of every ``=`` and ``/=`` rule."""
        alternatives = []
        for rule in self.rules:
            if isinstance(rule, TypeRule):
                alternatives.extend(rule.type.alternatives)
        span = self.rules[0].span if self.rules else Span(0, 0)
        return Type(tuple(alternatives), span=span)

    def group(self) -> Group:
        """The rule's group: one choice per ``=`` and ``//=`` rule.

        ``g = (a, b // c)`` contributes both of its choices. A type rule that
        was reclassified as a group (``g = (other-group)``) contributes a
        reference to that group.
        """
        choices: List[GroupChoice] = []
        for rule in self.rules:
            entry = rule.entry if isinstance(rule, GroupRule) else _as_group_entry(rule)
            if isinstance(entry, InlineGroup) and entry.occurrence is None:
                choices.extend(entry.group.choices)
            else:
                choices.append(GroupChoice((entry,), span=entry.span))
        span = self.rules[0].span if self.rules else Span(0, 0)
        return Group(tuple(choices), span=span)

    def __repr__(self) -> str:
        params = '<' + ', '.join(self.params) + '>' if self.params else ''
        return f"RuleDef({self.name}{params}, {self.kind}, {len(self.rules)} rule(s))"


class ResolvedSchema:
    """A schema whose names have been checked and can be looked up.

    Attributes:
        schema:  The parsed user schema.
        rules:   ``name -> RuleDef`` for user rules and the prelude rules they
                 do not redefine, user rules first, in source order.
        root:    The first rule defined with ``=`` (the default root), or
                 ``None`` for a schema with no such rule.
        external: Undefined names allowed because the schema imports the
                 module they come from (``coswid.tag-id``).
    """

    def __init__(self, schema: Schema, rules: Dict[str, RuleDef], root: Optional[str],
                 external: FrozenSet[str] = frozenset()):
        self.schema = schema
        self.rules = rules
        self.root = root
        self.external = external
        self._instances: Dict[Tuple[str, tuple], Union[Type, Group]] = {}

    def lookup(self, name: str) -> Optional[RuleDef]:
        """The definition of *name*, or ``None`` (undefined, or an empty socket)."""
        return self.rules.get(name)

    def kind(self, name: str) -> Optional[str]:
        """``"type"`` or ``"group"`` for a defined name or a socket, else ``None``."""
        return _kind(self.rules, name)

    def is_group(self, name: str) -> bool:
        return self.kind(name) == GROUP

    def instantiate(self, name: str, args: Tuple = ()) -> Optional[Union[Type, Group]]:
        """The definition of *name* with its generic parameters replaced by *args*.

        Returns the rule's ``Type`` for a type rule and its ``Group`` for a
        group rule, or ``None`` for an undefined name or an empty socket.
        Results are memoized; argument nodes compare equal whatever their
        spans, so ``pair<uint, tstr>`` is instantiated once.
        """
        rule = self.rules.get(name)
        if rule is None:
            return None
        args = tuple(args)
        if len(args) != len(rule.params):
            raise ValueError(f"{name} takes {len(rule.params)} generic argument(s), "
                             f"got {len(args)}")
        key = (name, args)
        cached = self._instances.get(key)
        if cached is None:
            body = rule.type() if rule.kind == TYPE else rule.group()
            cached = substitute(body, dict(zip(rule.params, args))) if args else body
            self._instances[key] = cached
        return cached

    def bits(self, node) -> Optional[int]:
        """The bit positions the argument *node* of ``.bits`` names, as a mask.

        ``None`` when they are not evident (see ``bit_mask``).
        """
        try:
            return bit_mask(self.rules, node)
        except ValueError:
            return None

    def literal(self, node):
        """The numeric, text or byte literal *node* stands for, following names.

        A literal node is returned as it is; a ``Name`` (or ``( name )``)
        whose rule is a single literal gives that literal. Anything else
        gives ``None``.
        """
        return _literal(self.rules, node)


# ----------------------------------------------------------------- resolve

class Imports:
    """The module imports of a schema that were *not* loaded.

    Attributes:
        prefixes: Prefixes of imports whose module is unavailable; any name
            ``prefix.x`` is accepted.
        any_name: Whether an unprefixed import or include is unavailable, so
            any undefined name is accepted.
        loaded:  ``prefix -> module name`` for modules that were merged in,
            to explain a missing rule.
    """

    def __init__(self, prefixes: FrozenSet[str] = frozenset(), any_name: bool = False,
                 loaded: Optional[Dict[str, str]] = None):
        self.prefixes = prefixes
        self.any_name = any_name
        self.loaded = loaded or {}


def resolve(schema: Schema, imports: Optional[Imports] = None) -> ResolvedSchema:
    """Check *schema* and return its resolved model.

    Args:
        imports: Which imports are unavailable (see :class:`Imports`). When
            omitted, every import declared in the schema text is treated as
            unavailable.

    Raises:
        CDDLSemanticError: (a ``SchemaError``) for the first problem found,
            with its line and column. Its ``errors`` holds every problem found
            (see :func:`resolve_all`).
    """
    resolved, errors = _Resolver(schema, imports).run()
    if resolved is None:
        raise errors[0]
    return resolved


def resolve_all(schema: Schema, imports: Optional[Imports] = None
                ) -> Tuple[Optional[ResolvedSchema], Tuple[CDDLSemanticError, ...]]:
    """Check *schema* like :func:`resolve`, but return every problem instead of raising.

    A problem in the names and the rule definitions (a name defined twice, a
    cycle) stops the resolver: it is returned alone. Problems in what a rule
    contains (an undefined name, a control that does not apply) do not: the
    check goes on with the next entry of the group, and the next rule, so each
    is reported, in source order. The first problem is the one :func:`resolve`
    raises.

    Returns:
        ``(resolved, ())`` for a schema that makes sense, else
        ``(None, errors)``.
    """
    return _Resolver(schema, imports).run()


class _Resolver:
    def __init__(self, schema: Schema, imports: Optional[Imports] = None):
        self.schema = schema
        self.source = schema.source
        self.rules: Dict[str, RuleDef] = {}
        if imports is None:
            prefixes, import_all = _imports(schema.source.text if schema.source else '')
            imports = Imports(prefixes, import_all)
        self.prefixes, self.import_all = imports.prefixes, imports.any_name
        self.loaded_modules = imports.loaded
        self.external: Set[str] = set()
        self.errors: List[CDDLSemanticError] = []  # problems found while checking the rules
        self.rule_name: Optional[str] = None  # the rule being checked

    def error(self, message: str, span: Span) -> CDDLSemanticError:
        return CDDLSemanticError(message, span, self.source, self.rule_name)

    def fail(self, message: str, span: Span) -> None:
        """Raise the problem *message* at *span* (for :meth:`collecting`)."""
        raise self.error(message, span)

    def collecting(self, check, *args) -> None:
        """Run *check*; keep the problem it finds instead of stopping, if it finds one."""
        try:
            check(*args)
        except CDDLSemanticError as exc:
            if not any(known.message == exc.message and known.span == exc.span
                       for known in self.errors):
                self.errors.append(exc)

    def where(self, span: Span) -> str:
        if self.source is None:
            return ''
        line, _ = self.source.line_col(span.start)
        return f' (line {line})'

    # ---------------------------------------------------------- symbols

    def run(self) -> Tuple[Optional[ResolvedSchema], Tuple[CDDLSemanticError, ...]]:
        try:
            root = self.collect()
            for name, rule in parse_prelude_rules().items():
                self.rules.setdefault(name, rule)
            self.classify()
            self.check_cycles()
        except CDDLSemanticError as exc:  # the rules are not known: nothing more can be checked
            return None, (exc,)
        for rule in self.rules.values():
            if not rule.prelude:
                self.rule_name = rule.name
                for definition in rule.rules:
                    self.collecting(self.check_rule, rule, definition)
        self.rule_name = None
        if self.errors:
            errors = tuple(self.errors)
            for error in errors:
                error.errors = errors
            return None, errors
        return ResolvedSchema(self.schema, self.rules, root, frozenset(self.external)), ()

    def collect(self) -> Optional[str]:
        """Build the symbol table and report conflicting definitions."""
        root = None
        bases: Dict[str, object] = {}
        for rule in self.schema.rules:
            name = rule.name
            self.check_params(rule)
            if rule.assign in ('/=', '//='):
                self.check_extension(rule)
            existing = self.rules.get(name)
            if rule.assign == '=':
                if root is None:
                    root = name
                if name in bases:
                    if bases[name] == rule:
                        continue  # the same rule written twice: harmless
                    raise self.error(
                        f"'{name}' is already defined{self.where(bases[name].span)}; "
                        f"use '/=' or '//=' to add alternatives", rule.span)
                bases[name] = rule
            if existing is None:
                kind = GROUP if isinstance(rule, GroupRule) else TYPE
                self.rules[name] = RuleDef(name, kind, rule.params, [rule])
                continue
            if rule.params != existing.params:
                raise self.error(
                    f"'{name}' is defined{self.where(existing.span)} with "
                    f"{_describe_params(existing.params)}, here with "
                    f"{_describe_params(rule.params)}", rule.span)
            if rule.assign == '=':
                existing.rules.insert(0, rule)  # the '=' rule comes first
            else:
                existing.rules.append(rule)
        return root

    def check_params(self, rule):
        seen: Set[str] = set()
        for param in rule.params:
            if param in seen:
                raise self.error(f"generic parameter '{param}' is listed twice in "
                                 f"'{rule.name}'", rule.span)
            seen.add(param)

    def check_extension(self, rule):
        name = rule.name
        if rule.assign == '/=' and name.startswith('$$'):
            raise self.error(f"'{name}' is a group socket: extend it with '//=', "
                             f"not '/='", rule.span)
        if rule.assign == '//=' and name.startswith('$') and not name.startswith('$$'):
            raise self.error(f"'{name}' is a type socket: extend it with '/=', "
                             f"not '//='", rule.span)

    def classify(self):
        """Decide the kind of every user rule and report type/group conflicts.

        A ``//=`` rule or a rule with member keys or occurrences is a group,
        a ``/=`` rule or any other type rule is a type. ``a = b`` and
        ``a = (b)`` take the kind of ``b``, and are types when ``b`` is
        undefined or is itself undecided.
        """
        pending = []
        for rule in self.rules.values():
            if rule.prelude:
                continue
            decided = None
            for definition in rule.rules:
                kind = _definite_kind(definition)
                if kind is None:
                    continue
                if decided is not None and kind != decided[0]:
                    what = 'a group' if decided[0] == GROUP else 'a type'
                    op = "'//='" if definition.assign == '//=' else (
                        "'/='" if definition.assign == '/=' else "'='")
                    raise self.error(f"'{rule.name}' is {what}"
                                     f"{self.where(decided[1].span)}, so it cannot "
                                     f"be defined as a {kind} with {op}",
                                     definition.span)
                decided = (kind, definition)
            if decided is None:
                rule.kind = TYPE
                pending.append(rule)
            else:
                rule.kind = decided[0]
        changed = True
        while changed:
            changed = False
            for rule in pending:
                target = _single_name(rule.rules[0])
                if rule.kind != GROUP and self.kind_of(target.name) == GROUP:
                    rule.kind = GROUP
                    changed = True

    def kind_of(self, name: str) -> Optional[str]:
        return _kind(self.rules, name)

    def check_cycles(self):
        """Reject rules that only rename each other (``a = b``, ``b = a``)."""
        # Alias chains commonly share suffixes. Cache chains proven to terminate
        # so each rule is followed at most once, rather than rescanning every
        # suffix and doing quadratic work on long rename chains.
        done: Set[str] = set()
        for start in self.rules.values():
            if start.prelude or start.name in done:
                continue
            chain: List[str] = []
            positions: Dict[str, int] = {}
            rule = start
            while rule is not None and rule.name not in done:
                if rule.name in positions:
                    # A cycle not starting at this rule will be reported when
                    # the cycle's own first rule is visited. Cache only the
                    # non-cyclic prefix; caching cycle members would hide it.
                    cycle_start = positions[rule.name]
                    done.update(chain[:cycle_start])
                    break
                positions[rule.name] = len(chain)
                chain.append(rule.name)
                target = _alias_target(rule)
                if target is None:
                    done.update(chain)
                    break
                if target in positions:
                    if target == start.name:
                        names = chain + [target]
                        if len(names) > 8:
                            names = names[:4] + ['...'] + names[-2:]
                        path = ' -> '.join(names)
                        raise self.error(f"'{start.name}' is defined only in terms of "
                                         f"itself ({path})", start.span)
                    # The repeated target begins a cycle beyond the start.
                    done.update(chain[:positions[target]])
                    break
                if target in done:
                    done.update(chain)
                    break
                rule = self.rules.get(target)
            else:
                # The chain reached a rule already checked as terminating.
                done.update(chain)

    # ------------------------------------------------------- references

    def reference(self, name: str, args: tuple, span: Span, scope: FrozenSet[str],
                  used_as: str) -> Optional[RuleDef]:
        """Check one reference and return its rule (``None``: param or socket)."""
        if name in scope:
            if args:
                raise self.error(f"generic parameter '{name}' takes no arguments", span)
            return None
        rule = self.rules.get(name)
        if rule is None:
            if name.startswith('$'):
                if used_as == TYPE and name.startswith('$$'):
                    raise self.error(f"'{name}' is a group socket and cannot be used "
                                     f"as a type", span)
                if args:
                    raise self.error(f"'{name}' is not generic, but is given "
                                     f"{len(args)} argument(s)", span)
                return None  # an empty socket
            if self.import_all or name.partition('.')[0] in self.prefixes:
                self.external.add(name)
                return None  # from an imported module
            raise self.error(self.undefined(name, scope), span)
        if len(args) != len(rule.params):
            if not rule.params:
                raise self.error(f"'{name}' is not generic, but is given "
                                 f"{len(args)} argument(s)", span)
            raise self.error(f"'{name}' takes {_describe_params(rule.params)}, "
                             f"but is given {len(args)}", span)
        return rule

    def undefined(self, name: str, scope: Iterable[str]) -> str:
        message = f"undefined name '{name}'"
        prefix, _, rule = name.partition('.')
        if rule and prefix in self.loaded_modules:
            return (message + f"; module '{self.loaded_modules[prefix]}' (imported as "
                    f"'{prefix}') defines no rule '{rule}'")
        if '..' in name:
            low, _, high = name.partition('..')
            return message + f"; write '{low} .. {high}' for a range"
        candidates = list(self.rules) + list(scope)
        close = difflib.get_close_matches(name, candidates, n=1)
        if close:
            message += f"; did you mean '{close[0]}'?"
        return message

    # ------------------------------------------------------------ rules

    def check_rule(self, rule: RuleDef, definition):
        scope = frozenset(definition.params)
        if isinstance(definition, GroupRule):
            self.check_entry(definition.entry, scope, None)
        elif rule.kind == GROUP:
            self.check_entry(_as_group_entry(definition), scope, None)
        else:
            self.check_type(definition.type, scope)

    def check_type(self, ty: Type, scope):
        for alternative in ty.alternatives:
            self.collecting(self.check_type1, alternative, scope)

    def check_type1(self, node, scope):
        before = len(self.errors)
        if isinstance(node, Range):
            self.collecting(self.check_type2, node.low, scope)
            self.collecting(self.check_type2, node.high, scope)
            if len(self.errors) == before:  # the bounds mean something
                self.check_range(node, scope)
        elif isinstance(node, Control):
            self.collecting(self.check_type1, node.target, scope)
            self.collecting(self.check_type2, node.arg, scope, node.op)
            if len(self.errors) == before:  # the target and the argument mean something
                self.check_control(node, scope)
        else:
            self.check_type2(node, scope)

    def check_type2(self, node, scope, control: Optional[str] = None):
        """Check one type2; *control* is the control whose argument it is, if it is one."""
        if isinstance(node, Name):
            rule = self.reference(node.name, node.args, node.span, scope, TYPE)
            if rule is not None and rule.kind == GROUP:
                raise self.error(f"'{node.name}' is a group and cannot be used as a type; "
                                 f"{_group_hint(node.name, control)}", node.span)
            self.check_args(node.args, scope)
            if node.args and rule is not None and rule.kind == TYPE:
                self.check_instance_controls(node, scope)
        elif isinstance(node, Paren):
            self.check_type(node.type, scope)
        elif isinstance(node, Map):
            self.check_group(node.group, scope, in_map=True)
        elif isinstance(node, Array):
            self.check_group(node.group, scope, in_map=False)
        elif isinstance(node, Unwrap):
            rule = self.reference(node.name, node.args, node.span, scope, TYPE)
            self.check_args(node.args, scope)
            if rule is not None:
                self.check_unwrap(node, rule)
        elif isinstance(node, ChoiceFrom):
            if node.group is not None:
                self.check_group(node.group, scope, in_map=None)
            else:
                rule = self.reference(node.name, node.args, node.span, scope, GROUP)
                self.check_args(node.args, scope)
                if rule is not None and rule.kind != GROUP:
                    raise self.error(f"'&{node.name}' needs a group, but '{node.name}' "
                                     f"is a type", node.span)
        elif isinstance(node, Tag):
            if isinstance(node.tag, Type):
                self.check_head(node.tag, scope, node.span)
            self.check_type(node.type, scope)
        elif isinstance(node, Major):
            if isinstance(node.info, Type):
                self.check_head(node.info, scope, node.span)

    def check_head(self, ty: Type, scope, span: Span):
        self.check_type(ty, scope)
        kinds = self.kinds(ty, scope)
        if kinds is not None and not kinds <= {'uint'}:
            raise self.error("the number in '#6.<...>' or '#7.<...>' must be an "
                             "unsigned integer type", span)

    def check_args(self, args, scope):
        for arg in args:
            if isinstance(arg, Name):
                # a generic argument may be a group, used as one in the body
                # ('entity-map<$role, $$extension>')
                self.reference(arg.name, arg.args, arg.span, scope, None)
                self.check_args(arg.args, scope)
            else:
                self.check_type1(arg, scope)

    def check_group(self, group: Group, scope, in_map: Optional[bool]):
        for choice in group.choices:
            for entry in choice.entries:
                self.collecting(self.check_entry, entry, scope, in_map)

    def check_entry(self, entry, scope, in_map: Optional[bool]):
        self.check_occurrence(entry.occurrence)
        if isinstance(entry, Member):
            if entry.key is not None:
                if entry.key.kind == 'value':
                    self.collecting(self.check_type2, entry.key.value, scope)
                elif entry.key.kind == 'type':
                    self.collecting(self.check_type1, entry.key.type, scope)
            elif in_map and not _is_unwrap(entry.value):
                self.collecting(self.fail, "a map entry needs a key: write 'key: type' or "
                                "'key-type => type'", entry.span)
            self.check_type(entry.value, scope)
            if in_map and entry.key is None and _is_unwrap(entry.value):
                self.check_map_splice(entry.value.alternatives[0])
        elif isinstance(entry, GroupRef):
            rule = self.reference(entry.name, entry.args, entry.span, scope, GROUP)
            self.check_args(entry.args, scope)
            kind = rule.kind if rule is not None else self.kind_of(entry.name)
            if in_map and kind == TYPE and entry.name not in scope:
                raise self.error(f"'{entry.name}' is a type, so it needs a key in a "
                                 f"map: write 'key: {entry.name}' or "
                                 f"'key-type => {entry.name}'", entry.span)
        elif isinstance(entry, InlineGroup):
            self.check_group(entry.group, scope, in_map)

    def check_occurrence(self, occurrence: Optional[Occurrence]):
        if occurrence is not None and occurrence.max is not None \
                and occurrence.min > occurrence.max:
            raise self.error(f"occurrence '{occurrence.text}' allows no entries: "
                             f"{occurrence.min} is more than {occurrence.max}",
                             occurrence.span)

    def check_map_splice(self, node: Unwrap):
        """``~name`` as a map entry splices the members of a map (#158)."""
        rule = self.rules.get(node.name)
        seen: Set[str] = set()
        while rule is not None and rule.name not in seen and not rule.params \
                and rule.kind == TYPE:
            seen.add(rule.name)
            alternatives = rule.type().alternatives
            if len(alternatives) != 1:
                return
            head = _strip_parens(alternatives[0])
            if isinstance(head, Tag):
                inner = head.type.alternatives
                head = _strip_parens(inner[0]) if len(inner) == 1 else None
            if isinstance(head, Array):
                raise self.error(f"'~{node.name}' is an array, so it cannot be spliced "
                                 f"into a map", node.span)
            if isinstance(head, Name) and not head.args:
                rule = self.rules.get(head.name)
                continue
            return

    def check_unwrap(self, node: Unwrap, rule: RuleDef):
        if rule.kind == GROUP:
            raise self.error(f"'~{node.name}' needs a map, array or tag, but "
                             f"'{node.name}' is a group", node.span)
        seen: Set[str] = set()
        while rule is not None and rule.name not in seen and not rule.params:
            seen.add(rule.name)
            alternatives = rule.type().alternatives
            if len(alternatives) != 1:
                break
            head = _strip_parens(alternatives[0])
            if isinstance(head, (Map, Array, Tag)):
                return
            if isinstance(head, Name) and not head.args:
                rule = self.rules.get(head.name)
                continue
            if isinstance(head, (IntLit, FloatLit, TextLit, BytesLit, Major, Range)):
                raise self.error(f"'~{node.name}' needs a map, array or tag, but "
                                 f"'{rule.name}' is not one", node.span)
            break

    # --------------------------------------------------- ranges, controls

    def check_range(self, node: Range, scope):
        bounds = []
        for bound in (node.low, node.high):
            if isinstance(bound, Name) and (bound.name in scope
                                             or bound.name.startswith('$')):
                return  # a generic parameter or socket: checked when used
            literal = self.literal(bound)
            if isinstance(literal, (TextLit, BytesLit)) or (
                    literal is None and isinstance(bound, (Map, Array, Tag, Major,
                                                           AnyItem, Unwrap, ChoiceFrom))):
                raise self.error("range bounds must be numbers", bound.span)
            if literal is None:
                if isinstance(bound, Name):
                    raise self.error(f"range bound '{bound.name}' is not a number",
                                     bound.span)
                return
            bounds.append(literal)
        low, high = bounds
        if type(low) is not type(high):
            raise self.error("range bounds must both be integers or both be floats",
                             node.span)
        op = '..' if node.inclusive else '...'
        if low.value > high.value or (not node.inclusive and low.value == high.value):
            raise self.error(f"range '{low.text}{op}{high.text}' is empty", node.span)

    def literal(self, node):
        return _literal(self.rules, node)

    def check_instance_controls(self, node: Name, scope):
        """Recheck the ``.eq``, ``.ne``, ``.bits`` and ``.regexp`` arguments of ``node`` once its generic arguments are in.

        In the definition ``g<T> = uint .eq T`` the argument ``T`` is unknown, so
        ``g<(1 / 2)>`` passes there; here the substituted argument is checked.
        Names are not followed, so a recursive generic ends.
        """
        rule = self.rules.get(node.name)
        if rule is None or len(node.args) != len(rule.params):
            return
        body = substitute(rule.type(), dict(zip(rule.params, node.args)))
        for sub in walk(body):
            if not isinstance(sub, Control):
                continue
            if sub.op in ('eq', 'ne') and self.one_value(sub.arg, scope) is False:
                raise self.error(f"the argument of '.{sub.op}' must be one value (a number, "
                                 f"a string, true, false, null or undefined, or a rule that is "
                                 f"one), not a type that matches several; '{node.name}' "
                                 f"passes one that matches several", node.span)
            if sub.op in _ARGUMENT_CHECKED:
                problem = self.pattern_problem(sub, scope)
                if problem:
                    raise self.error(f"{problem}; '{node.name}' passes it", node.span)

    def open_names(self, node, scope) -> bool:
        """Whether *node* mentions a generic parameter or a name this schema does not define.

        Such a node cannot be computed with yet: a parameter is known where the generic rule
        is used, and an undefined name is reported on its own.
        """
        return any(isinstance(sub, (Name, GroupRef)) and (sub.name in scope or sub.name not in self.rules)
                   for sub in walk(node))

    def string_literal(self, node, op: str, what: str):
        """``(text, problem)``: the one text string *node* computes to, else why not."""
        try:
            items = computed.evaluate(self.rules, node)
        except computed.ComputeError as exc:
            return None, f"{what} of '.{op}' must be written as text (or computed from text): {exc}"
        one = computed.single(items)
        if one is None or one.kind != 'text':
            return None, f"{what} of '.{op}' must be one text string"
        return one.value, None

    def entries_problem(self, node: Control, scope, op: str) -> Optional[str]:
        """Why the array that is the argument of ``.printf`` or ``.join`` cannot be followed."""
        if self.open_names(node.arg, scope):
            return None
        try:
            entries = computed.array_entries(self.rules, node.arg)
        except computed.ComputeError as exc:
            return f"the argument of '.{op}' cannot be followed: {exc}"
        if entries is None:
            return f"the argument of '.{op}' must be an array that is written out"
        for _entry, occurrence in entries[(1 if op == 'printf' else 0):]:
            if occurrence is not None:
                return (f"the elements of the argument of '.{op}' must be written out, each "
                        f"once: '{occurrence.text}' cannot be followed")
        if op == 'printf':
            if not entries:
                return "the argument of '.printf' is an array of a format string and its data items"
            fmt, problem = self.string_literal(entries[0][0], op, 'the format string')
            if problem:
                return problem
            try:
                parts = conversions.parse_format(fmt)
            except conversions.PrintfError as exc:
                return f"the format string of '.printf' is not usable: {exc}"
            wanted = conversions.item_count(parts)
            if wanted != len(entries) - 1:
                return (f"the format string of '.printf' has {wanted} data item(s) (one for each "
                        f"conversion and each '*') but the array gives {len(entries) - 1}")
        else:
            for entry, _occurrence in entries:
                kinds = self.kinds(entry, scope)
                if kinds is not None and not kinds <= {'tstr', 'bstr'}:
                    return (f"the elements of the argument of '.join' must be strings, not "
                            f"{_describe_kinds(kinds)}")
        return None

    def pattern_problem(self, node: Control, scope) -> Optional[str]:
        """Why the argument of a control that is read at validation time is wrong.

        That is the XSD pattern of ``.regexp``, the bit positions of ``.bits``, the ABNF of
        ``.abnf``, the operands of ``.plus``, ``.cat`` and ``.det`` and the arrays of
        ``.printf`` and ``.join``.
        """
        if node.op in ('abnf', 'abnfb'):
            if self.open_names(node.arg, scope):
                return None
            text, problem = self.string_literal(node.arg, node.op, 'the argument')
            if problem:
                return problem
            try:
                abnf.compile_abnf(text)
            except abnf.AbnfError as exc:
                return f"the argument of '.{node.op}' is not usable ABNF: {exc}"
        elif node.op in computed.OPERATORS:
            if self.open_names(node, scope):
                return None
            try:
                computed.evaluate(self.rules, node)
            except computed.ComputeError as exc:
                return f"'.{node.op}' cannot be computed: {exc}"
        elif node.op in ('printf', 'join'):
            return self.entries_problem(node, scope, node.op)
        elif node.op == 'feature':
            if self.open_names(node.arg, scope):
                return None
            try:
                computed.feature(self.rules, node.arg)
            except computed.ComputeError as exc:
                return (f"the argument of '.feature' must be a feature name (a text string) or "
                        f"an array of the name and a detail: {exc}")
        elif node.op == 'regexp':
            pattern = self.literal(node.arg)
            if isinstance(pattern, TextLit):
                try:
                    xsd_regex.compile_pattern(pattern.value)
                except xsd_regex.XsdRegexError as exc:
                    return f"the argument of '.regexp' is not a valid XSD regular expression: {exc}"
        elif node.op == 'bits':
            try:
                bit_mask(self.rules, node.arg, scope)
            except ValueError as exc:
                return (f"the argument of '.bits' must be bit positions "
                        f"(a uint, a range or a choice of them): {exc}")
        return None

    def check_control(self, node: Control, scope):
        op = node.op
        if op not in KNOWN_CONTROLS:
            close = difflib.get_close_matches(op, KNOWN_CONTROLS, n=1)
            hint = f"; did you mean '.{close[0]}'?" if close else ''
            raise self.error(f"unknown control operator '.{op}'{hint}", node.span)
        allowed_target, allowed_arg = _CONTROL_KINDS.get(op, (None, None))
        if op in ('eq', 'ne') and self.one_value(node.arg, scope) is False:
            raise self.error(f"the argument of '.{op}' must be one value (a number, a string, "
                             f"true, false, null or undefined, or a rule that is one), "
                             f"not a type that matches several", node.arg.span)
        problem = self.pattern_problem(node, scope)
        if problem:
            raise self.error(problem, node.arg.span)
        if allowed_target is not None:
            kinds = self.kinds(node.target, scope)
            if kinds is not None and not kinds & allowed_target:
                raise self.error(f"'.{op}' cannot be applied to "
                                 f"{_describe_kinds(kinds)}", node.span)
        if allowed_arg is not None:
            kinds = self.kinds(node.arg, scope)
            if kinds is not None and not kinds <= allowed_arg:
                expected = _describe_kinds(allowed_arg, ' or ')
                raise self.error(f"the argument of '.{op}' must be {expected}, "
                                 f"not {_describe_kinds(kinds)}", node.arg.span)

    def one_value(self, node, scope, seen: Optional[Set[str]] = None) -> Optional[bool]:
        """Whether *node* stands for exactly one value: ``None`` if not evident.

        A literal, ``true``, ``false``, ``null`` or ``undefined`` is one value, so
        is a rule (or parenthesized rule) whose only alternative is. A type
        that matches many values (``uint``, a map, a range, a choice) is not.
        A generic parameter, or a name the resolver does not know, is not evident.
        """
        seen = set() if seen is None else seen
        if isinstance(node, Type):
            if len(node.alternatives) != 1:
                return False
            return self.one_value(node.alternatives[0], scope, seen)
        if isinstance(node, (IntLit, FloatLit, TextLit, BytesLit)):
            return True
        if isinstance(node, Paren):
            return self.one_value(node.type, scope, seen)
        if isinstance(node, Major):
            return node.major == 7 and isinstance(node.info, int) and node.info in (20, 21, 22, 23)
        if isinstance(node, Name):
            if node.args or node.name in scope or node.name in seen \
                    or len(seen) >= _MAX_ALIAS_DEPTH:
                return None
            rule = self.rules.get(node.name)
            if rule is None or rule.kind != TYPE or rule.params:
                return None
            seen.add(node.name)
            try:
                return self.one_value(rule.type(), scope, seen)
            finally:
                seen.discard(node.name)
        return False  # a range, map, array, tag, control, ...

    def kinds(self, node, scope, seen: Optional[Set[str]] = None) -> Optional[FrozenSet[str]]:
        """The kinds of data item *node* can match, or ``None`` if not evident."""
        seen = set() if seen is None else seen
        if isinstance(node, Type):
            result: Set[str] = set()
            for alternative in node.alternatives:
                kinds = self.kinds(alternative, scope, seen)
                if kinds is None:
                    return None
                result |= kinds
            return frozenset(result)
        if isinstance(node, IntLit):
            return frozenset({'uint' if node.value >= 0 else 'nint'})
        if isinstance(node, FloatLit):
            return frozenset({'float'})
        if isinstance(node, TextLit):
            return frozenset({'tstr'})
        if isinstance(node, BytesLit):
            return frozenset({'bstr'})
        if isinstance(node, Map):
            return frozenset({'map'})
        if isinstance(node, Array):
            return frozenset({'array'})
        if isinstance(node, Tag):
            return frozenset({'tag'})
        if isinstance(node, Major):
            return _major_kinds(node)
        if isinstance(node, Paren):
            return self.kinds(node.type, scope, seen)
        if isinstance(node, Range):
            low = self.kinds(node.low, scope, seen)
            high = self.kinds(node.high, scope, seen)
            if low is None or high is None:
                return None
            return low | high
        if isinstance(node, Control):
            return self.kinds(node.target, scope, seen)
        if isinstance(node, Name):
            if node.args or node.name in scope or node.name in seen \
                    or len(seen) >= _MAX_ALIAS_DEPTH:
                return None
            rule = self.rules.get(node.name)
            if rule is None or rule.kind != TYPE or rule.params:
                return None
            seen.add(node.name)
            try:
                return self.kinds(rule.type(), scope, seen)
            finally:
                seen.discard(node.name)
        return None  # '#', '~x', '&x': not evident


def parse_prelude_rules() -> Dict[str, RuleDef]:
    """``RuleDef`` objects for the prelude, built fresh (they are mutable)."""
    rules: Dict[str, RuleDef] = {}
    for rule in parse_prelude().rules:
        rules[rule.name] = RuleDef(rule.name, TYPE, rule.params, [rule], prelude=True)
    return rules


# ------------------------------------------------------------- substitution

def substitute(node, env: Dict[str, object]):
    """A copy of *node* with each generic parameter in *env* replaced.

    Parameters are replaced where they appear as a name with no arguments, as
    a group entry, as ``~P`` or ``&P``. A ``Range`` or ``Control`` argument
    substituted into a place that takes only a simple type is put in
    parentheses.
    """
    return _subst(node, env, wide=True)


_WIDE_FIELDS = {('Type', 'alternatives'), ('Name', 'args'), ('Unwrap', 'args'),
                ('ChoiceFrom', 'args'), ('GroupRef', 'args'), ('MemberKey', 'type'),
                ('Control', 'target')}


def _subst(node, env, wide: bool):
    if isinstance(node, Name) and node.name in env and not node.args:
        arg = env[node.name]
        if not wide and isinstance(arg, (Range, Control)):
            return Paren(Type((arg,), span=arg.span), span=node.span)
        return arg
    if isinstance(node, GroupRef) and node.name in env and not node.args:
        arg = env[node.name]
        if isinstance(arg, Name):
            return GroupRef(node.occurrence, arg.name, arg.args, node.comment, span=node.span)
        return Member(node.occurrence, None, Type((arg,), span=arg.span), node.comment,
                      span=node.span)
    if isinstance(node, (Unwrap, ChoiceFrom)) and node.name in env and not node.args:
        arg = env[node.name]
        if isinstance(arg, Name):
            return replace(node, name=arg.name, args=arg.args)
        if isinstance(node, ChoiceFrom) and isinstance(arg, (Map, Array)):
            return ChoiceFrom(group=arg.group, span=node.span)
        return node
    if not hasattr(node, '__dataclass_fields__') or isinstance(node, (Span, Schema)):
        return node
    changes = {}
    cls = type(node).__name__
    for f in fields(node):
        if f.name in ('span', 'comment'):
            continue
        value = getattr(node, f.name)
        child_wide = (cls, f.name) in _WIDE_FIELDS
        if isinstance(value, tuple):
            new = tuple(_subst(item, env, child_wide) for item in value)
        else:
            new = _subst(value, env, child_wide)
        if new is not value:
            changes[f.name] = new
    return replace(node, **changes) if changes else node


# ------------------------------------------------------------------ helpers

_IMPORT = re.compile(r'^[ \t]*;#[ \t]*(import|include)[ \t]+([^\s;]+)'
                     r'(?:[ \t]+as[ \t]+([^\s;]+))?', re.MULTILINE)


def import_directives(text: str) -> List[Tuple[str, str, Optional[str], int, int]]:
    """``(kind, module, prefix, start, end)`` of each ``;# import`` / ``;# include`` comment.

    ``start`` and ``end`` are code-point offsets into *text*: ``start`` is at the
    ``;#`` (not at the indent before it) and ``end`` after the last word of the
    directive. Repeats are kept, so each one can be located.
    """
    found = []
    for match in _IMPORT.finditer(text):
        text_of = match.group(0)
        start = match.start() + len(text_of) - len(text_of.lstrip(' \t'))
        found.append((match.group(1), match.group(2), match.group(3), start, match.end()))
    return found


def parse_imports(text: str) -> List[Tuple[str, str, Optional[str]]]:
    """``(kind, module, prefix)`` of each ``;# import`` / ``;# include`` comment.

    ``kind`` is ``"import"`` or ``"include"``; ``prefix`` is the name after
    ``as``, or ``None``. Repeats are dropped.
    """
    found: List[Tuple[str, str, Optional[str]]] = []
    for kind, module, prefix, _start, _end in import_directives(text):
        item = (kind, module, prefix)
        if item not in found:
            found.append(item)
    return found


def _imports(text: str) -> Tuple[FrozenSet[str], bool]:
    """Module prefixes declared by ``;# import X as P``, and whether any
    import or include has no prefix (so any undefined name may come from it)."""
    prefixes: Set[str] = set()
    unprefixed = False
    for match in _IMPORT.finditer(text):
        if match.group(3):
            prefixes.add(match.group(3))
        else:
            unprefixed = True
    return frozenset(prefixes), unprefixed


def _kind(rules: Dict[str, RuleDef], name: str) -> Optional[str]:
    rule = rules.get(name)
    if rule is not None:
        return rule.kind
    if name.startswith('$$'):
        return GROUP  # an empty group socket
    if name.startswith('$'):
        return TYPE  # an empty type socket
    return None


_MAX_BIT = 1 << 16  # the highest bit position `.bits` accepts


def bit_mask(rules: Dict[str, RuleDef], node, scope=frozenset(), seen: Optional[Set[str]] = None):
    """The bit positions *node* names, as an integer with those bits set.

    This is the argument of ``.bits`` (RFC 8610 section 3.8): an unsigned integer
    is one position, a range ``a..b`` is every position in it, and a choice or
    an enumeration (``&(a: 0, b: 2)``) is the union of its members. A rule
    that stands for one of these is followed. Returns ``None`` when this is not
    evident (a generic parameter, an unknown name) and raises ``ValueError``
    naming the problem when *node* is not a set of positions.
    """
    seen = set() if seen is None else seen
    if isinstance(node, Type):
        mask = 0
        for alternative in node.alternatives:
            part = bit_mask(rules, alternative, scope, seen)
            if part is None:
                return None
            mask |= part
        return mask
    if isinstance(node, IntLit):
        if node.value < 0 or node.value >= _MAX_BIT:
            raise ValueError(f"bit position {node.text} is not between 0 and {_MAX_BIT - 1}")
        return 1 << node.value
    if isinstance(node, Range):
        low, high = _strip_parens(node.low), _strip_parens(node.high)
        if not isinstance(low, IntLit) or not isinstance(high, IntLit):
            return None
        last = high.value if node.inclusive else high.value - 1
        if low.value < 0 or last >= _MAX_BIT:
            raise ValueError(f"bit positions {low.text}{'..' if node.inclusive else '...'}"
                             f"{high.text} are not between 0 and {_MAX_BIT - 1}")
        return ((1 << (last + 1)) - 1) & ~((1 << low.value) - 1) if last >= low.value else 0
    if isinstance(node, Paren):
        return bit_mask(rules, node.type, scope, seen)
    if isinstance(node, ChoiceFrom) and node.group is not None:
        return _group_bit_mask(rules, node.group, scope, seen)
    if isinstance(node, (Name, ChoiceFrom)):
        name = node.name
        if node.args or name in scope or name in seen or len(seen) >= _MAX_ALIAS_DEPTH:
            return None
        rule = rules.get(name)
        if rule is None or rule.params:
            return None
        if isinstance(node, Name) and rule.kind != TYPE:
            return None
        seen.add(name)
        try:
            if isinstance(node, ChoiceFrom):  # '&name': the values of a group
                return _group_bit_mask(rules, rule.group(), scope, seen)
            return bit_mask(rules, rule.type(), scope, seen)
        finally:
            seen.discard(name)
    what = {TextLit: 'a text string', BytesLit: 'a byte string', FloatLit: 'a float',
            Map: 'a map', Array: 'an array', Tag: 'a tag', Control: 'a type with a control',
            Major: 'a data item'}.get(type(node), 'this')
    raise ValueError(f"{what} is not a bit position, a range or a choice of them")


def _group_bit_mask(rules, group: Group, scope, seen):
    mask = 0
    for choice in group.choices:
        for entry in choice.entries:
            part = _entry_bit_mask(rules, entry, scope, seen)
            if part is None:
                return None
            mask |= part
    return mask


def _entry_bit_mask(rules, entry, scope, seen):
    if isinstance(entry, Member):
        return bit_mask(rules, entry.value, scope, seen)
    if isinstance(entry, InlineGroup):
        return _group_bit_mask(rules, entry.group, scope, seen)
    if isinstance(entry, GroupRef):
        return bit_mask(rules, Name(entry.name, entry.args), scope, seen)
    return None


def _literal(rules: Dict[str, RuleDef], node):
    seen: Set[str] = set()
    while True:
        if isinstance(node, (IntLit, FloatLit, TextLit, BytesLit)):
            return node
        if isinstance(node, Paren) and len(node.type.alternatives) == 1:
            node = node.type.alternatives[0]
            continue
        if isinstance(node, Name) and not node.args and node.name not in seen:
            seen.add(node.name)
            rule = rules.get(node.name)
            if rule is None or rule.kind != TYPE or rule.params:
                return None
            alternatives = rule.type().alternatives
            if len(alternatives) != 1:
                return None
            node = alternatives[0]
            continue
        return None


def _is_unwrap(type_node) -> bool:
    """Whether *type_node* is just ``~name``."""
    return len(type_node.alternatives) == 1 and isinstance(type_node.alternatives[0], Unwrap)


def _strip_parens(node):
    while isinstance(node, Paren) and len(node.type.alternatives) == 1:
        node = node.type.alternatives[0]
    return node


def _single_name(rule) -> Optional[Name]:
    """The one name a ``maybe_group`` type rule stands for."""
    if not isinstance(rule, TypeRule) or not rule.maybe_group:
        return None
    head = _strip_parens(rule.type.alternatives[0])
    return head if isinstance(head, Name) else None


def _definite_kind(rule) -> Optional[str]:
    """The kind *rule* gives its name on its own, or ``None`` for ``a = b``."""
    if isinstance(rule, GroupRule):
        return GROUP
    if rule.assign == '=' and rule.maybe_group:
        return None
    return TYPE


def _as_group_entry(rule: TypeRule):
    """The group entry a reclassified ``a = b`` / ``a = (b)`` rule stands for."""
    name = _single_name(rule)
    return GroupRef(None, name.name, name.args, span=name.span)


def _alias_target(rule: RuleDef) -> Optional[str]:
    """The name a rule is nothing but, if it is a plain rename."""
    if len(rule.rules) != 1:
        return None
    definition = rule.rules[0]
    if isinstance(definition, TypeRule):
        if len(definition.type.alternatives) != 1:
            return None
        head = _strip_parens(definition.type.alternatives[0])
        if isinstance(head, Name) and not head.args and not rule.params:
            return head.name
        return None
    entry = definition.entry
    while isinstance(entry, InlineGroup) and entry.occurrence is None \
            and len(entry.group.choices) == 1 and len(entry.group.choices[0].entries) == 1:
        entry = entry.group.choices[0].entries[0]
    if isinstance(entry, GroupRef) and entry.occurrence is None and not entry.args \
            and not rule.params:
        return entry.name
    return None


def _major_kinds(node: Major) -> Optional[FrozenSet[str]]:
    base = {0: 'uint', 1: 'nint', 2: 'bstr', 3: 'tstr', 4: 'array', 5: 'map', 6: 'tag'}
    if node.major in base:
        return frozenset({base[node.major]})
    if not isinstance(node.info, int):
        return None
    if node.info in (25, 26, 27):
        return frozenset({'float'})
    if node.info in (20, 21):
        return frozenset({'bool'})
    if node.info == 22:
        return frozenset({'nil'})
    if node.info == 23:
        return frozenset({'undefined'})
    return frozenset({'simple'})


def _describe_kinds(kinds: Iterable[str], joiner: str = ' or ') -> str:
    names = sorted(_KIND_NAMES.get(k, k) for k in kinds)
    return joiner.join(names)


def _describe_params(params: Tuple[str, ...]) -> str:
    if not params:
        return 'no generic parameters'
    n = len(params)
    return f"{n} generic argument{'s' if n != 1 else ''} (<{', '.join(params)}>)"
