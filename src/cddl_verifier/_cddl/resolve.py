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

Names from other modules are not checked. A schema that declares an import
in the comment syntax of the CDDL modules draft (draft-ietf-cbor-cddl-modules),
``;# import rfc9393 as coswid``, may use ``coswid.anything``; one with an
unprefixed ``;# import name`` or ``;# include name`` may use any undefined
name. Such names are listed in ``ResolvedSchema.external`` and stand for
unknown types, since the module is not loaded.

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
                  Type, TypeRule, Unwrap)
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
    'cborseq': (frozenset({'bstr'}), None),
    'lt': (_NUMERIC, _NUMERIC),
    'le': (_NUMERIC, _NUMERIC),
    'gt': (_NUMERIC, _NUMERIC),
    'ge': (_NUMERIC, _NUMERIC),
    'abnf': (frozenset({'tstr'}), frozenset({'tstr'})),
    'abnfb': (frozenset({'bstr'}), frozenset({'tstr'})),
}

_KIND_NAMES = {
    'uint': 'an unsigned integer', 'nint': 'a negative integer', 'float': 'a float',
    'tstr': 'a text string', 'bstr': 'a byte string', 'array': 'an array',
    'map': 'a map', 'tag': 'a tagged item', 'bool': 'a boolean', 'nil': 'null',
    'undefined': 'undefined', 'simple': 'a simple value',
}

TYPE, GROUP = 'type', 'group'


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

    def literal(self, node):
        """The numeric, text or byte literal *node* stands for, following names.

        A literal node is returned as it is; a ``Name`` (or ``( name )``)
        whose rule is a single literal gives that literal. Anything else
        gives ``None``.
        """
        return _literal(self.rules, node)


# ----------------------------------------------------------------- resolve

def resolve(schema: Schema) -> ResolvedSchema:
    """Check *schema* and return its resolved model.

    Raises:
        CDDLSemanticError: (a ``SchemaError``) for the first problem found,
            with its line and column.
    """
    return _Resolver(schema).run()


class _Resolver:
    def __init__(self, schema: Schema):
        self.schema = schema
        self.source = schema.source
        self.rules: Dict[str, RuleDef] = {}
        self.prefixes, self.import_all = _imports(schema.source.text if schema.source else '')
        self.external: Set[str] = set()

    def error(self, message: str, span: Span) -> CDDLSemanticError:
        return CDDLSemanticError(message, span, self.source)

    def where(self, span: Span) -> str:
        if self.source is None:
            return ''
        line, _ = self.source.line_col(span.start)
        return f' (line {line})'

    # ---------------------------------------------------------- symbols

    def run(self) -> ResolvedSchema:
        root = self.collect()
        for name, rule in parse_prelude_rules().items():
            self.rules.setdefault(name, rule)
        self.classify()
        self.check_cycles()
        for rule in self.rules.values():
            if not rule.prelude:
                for definition in rule.rules:
                    self.check_rule(rule, definition)
        return ResolvedSchema(self.schema, self.rules, root, frozenset(self.external))

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
        for start in self.rules.values():
            if start.prelude:
                continue
            chain = [start.name]
            rule = start
            while True:
                target = _alias_target(rule)
                if target is None:
                    break
                if target == start.name:
                    names = chain + [target]
                    if len(names) > 8:
                        names = names[:4] + ['...'] + names[-2:]
                    path = ' -> '.join(names)
                    raise self.error(f"'{start.name}' is defined only in terms of "
                                     f"itself ({path})", start.span)
                if target in chain:
                    break  # a loop further on; reported for its own rules
                chain.append(target)
                rule = self.rules.get(target)
                if rule is None:
                    break

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
            self.check_type1(alternative, scope)

    def check_type1(self, node, scope):
        if isinstance(node, Range):
            self.check_type2(node.low, scope)
            self.check_type2(node.high, scope)
            self.check_range(node, scope)
        elif isinstance(node, Control):
            self.check_type1(node.target, scope)
            self.check_type2(node.arg, scope)
            self.check_control(node, scope)
        else:
            self.check_type2(node, scope)

    def check_type2(self, node, scope):
        if isinstance(node, Name):
            rule = self.reference(node.name, node.args, node.span, scope, TYPE)
            if rule is not None and rule.kind == GROUP:
                raise self.error(f"'{node.name}' is a group and cannot be used as a "
                                 f"type; wrap it as '{{ {node.name} }}' or "
                                 f"'[ {node.name} ]'", node.span)
            self.check_args(node.args, scope)
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
                self.check_entry(entry, scope, in_map)

    def check_entry(self, entry, scope, in_map: Optional[bool]):
        self.check_occurrence(entry.occurrence)
        if isinstance(entry, Member):
            if entry.key is not None:
                if entry.key.kind == 'value':
                    self.check_type2(entry.key.value, scope)
                elif entry.key.kind == 'type':
                    self.check_type1(entry.key.type, scope)
            elif in_map:
                raise self.error("a map entry needs a key: write 'key: type' or "
                                 "'key-type => type'", entry.span)
            self.check_type(entry.value, scope)
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

    def check_control(self, node: Control, scope):
        op = node.op
        if op not in KNOWN_CONTROLS:
            close = difflib.get_close_matches(op, KNOWN_CONTROLS, n=1)
            hint = f"; did you mean '.{close[0]}'?" if close else ''
            raise self.error(f"unknown control operator '.{op}'{hint}", node.span)
        allowed_target, allowed_arg = _CONTROL_KINDS.get(op, (None, None))
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
