"""Build the legacy ``CDDLParser`` tables from the AST (#110, Phase B of #69).

The validator and EDN generator still read the dictionaries the line-based
parser used to produce (``types``, ``type_aliases``, ``type_choices``,
``socket_extensions``, ``groups``, ``registered_params``,
``first_definition``). This module builds them from a parsed ``Schema`` so
those consumers keep working while they move to reading nodes (Phase C,
#111). Type expressions are written with :func:`format_node`, so consumers
always see canonical text.

Two things the validator cannot read from text get a table entry instead:

* An inline map (``a: { b: uint }``), or a map or array inside a tag
  (``#6.563([ value: bytes ])``), becomes a type of its own named
  ``<owner>@<path>`` (``r@a``), and the text refers to that name (#100).
* A map member with a computed key (``* tstr => any``) is listed in the
  map's ``computed_keys``; the validator accepts extra keys that match one
  (#104).

With the resolved model from #70, a rule is a group or a type as the
resolver decided (``h = ( name )`` is a group only if ``name`` is one), and a
reference to a generic rule (``pair<uint, tstr>``) is replaced by the rule
with its parameters substituted.

See ``docs/CDDL_AST_DESIGN.md`` §10.
"""

import re
from typing import Any, Dict, List, Optional

from .ast import (Array, Control, GroupRule, InlineGroup, IntLit, Map, Member, Name, Paren,
                  Tag, TextLit, Type, TypeRule, entry_type, registered_label, walk)
from .parser import MAX_DEPTH
from .printer import format_node

#: How many generic instances may be expanding inside each other. Past it,
#: a reference is kept as text, so 'g<T> = { n: g<[T]> }' terminates.
_MAX_INSTANCE_DEPTH = 16

# Occurrences that make a map member optional.
_OPTIONAL = ('?', '*')


class LegacyTables:
    """The dictionaries ``CDDLParser`` exposes, built from a ``Schema``."""

    def __init__(self, schema, resolved=None):
        #: ``ResolvedSchema`` (#70), used for rule kinds and generics.
        self.resolved = resolved
        self.types: Dict[str, Dict] = {}
        self.groups: Dict[str, List[str]] = {}
        self.type_choices: Dict[str, List[str]] = {}
        self.socket_extensions: Dict[str, List[str]] = {}
        self.registered_params: Dict[Any, str] = {}
        self.type_aliases: Dict[str, str] = {}
        self.first_definition: Optional[str] = None
        #: Names given to inline maps and to structures inside tags (``r@a``).
        self.synthetic_types: set = set()
        # Generic instances (name, args): the synthetic type built for each,
        # and those being expanded now, so recursive generics terminate.
        self._instance_names: Dict[tuple, str] = {}
        self._expanding: set = set()
        self._rule_names = {rule.name for rule in schema.rules}

        for rule in schema.rules:
            if isinstance(rule, TypeRule):
                self._type_rule(rule)
            else:
                self._group_rule(rule)

        # "IANA registered parameters" (&(name: N) => T) anywhere in the schema.
        for node in walk(schema):
            if isinstance(node, Member):
                label = registered_label(node)
                if label is not None:
                    key = _literal_key(label[1])
                    if key is not None:
                        self.registered_params[key] = label[0]

    # ------------------------------------------------------------- rules

    def _type_rule(self, rule: TypeRule):
        name = rule.name
        if rule.assign == '/=':
            choices = self.type_choices.setdefault(name, [])
            for alt in rule.type.alternatives:
                choices.append(self._text(Type((alt,)), f'{name}@{len(choices)}'))
            return
        if self.first_definition is None and not self._is_group(rule):
            self.first_definition = name

        alt = rule.type.alternatives[0]
        if self._is_group(rule):
            # 'g = ( name )' or 'g = name' where 'name' is a group
            inner = alt.type if isinstance(alt, Paren) else rule.type
            self.groups[name] = [format_node(inner)]
            return
        ty = rule.type
        if rule.maybe_group and isinstance(alt, Paren):
            ty = alt.type  # 'h = ( name )' where 'name' is a type
        expanded = ty if rule.params else self._expand(ty)
        structure = _structure(expanded, instance=expanded is not ty)
        ty = expanded
        if structure is not None:
            self.types[name] = self._structure_def(structure, name)
        else:
            self.type_aliases[name] = self._text(ty, name)

    def _is_group(self, rule: TypeRule) -> bool:
        """Whether a type rule the parser could not classify names a group."""
        if not rule.maybe_group:
            return False
        if self.resolved is None:
            # without resolution, 'g = ( name )' is read as a group
            return isinstance(rule.type.alternatives[0], Paren)
        return self.resolved.is_group(rule.name)

    def _expand(self, ty: Type, depth: int = 0) -> Type:
        """*ty* with a lone generic reference (``pair<A, B>``) instantiated."""
        if self.resolved is None or len(ty.alternatives) != 1 or depth > 8:
            return ty
        node = ty.alternatives[0]
        if not isinstance(node, Name) or not node.args:
            return ty
        rule = self.resolved.lookup(node.name)
        if rule is None or rule.kind != 'type' or len(rule.params) != len(node.args):
            return ty
        return self._expand(self.resolved.instantiate(node.name, node.args), depth + 1)

    def _group_rule(self, rule: GroupRule):
        if rule.assign == '//=':
            self.socket_extensions.setdefault(rule.name, []).append(format_node(rule.entry))
            return
        entry = rule.entry
        if isinstance(entry, InlineGroup) and entry.occurrence is None:
            self.groups[rule.name] = [format_node(e) for e in _entries(entry.group)]
        else:
            self.groups[rule.name] = [format_node(entry)]

    # -------------------------------------------------------- structures

    def _structure_def(self, node, owner: str) -> Dict:
        if isinstance(node, Map):
            return self._map(node.group, owner)
        return self._array(node.group, owner)

    def _map(self, group, owner: str) -> Dict:
        type_def = {'fields': {}, 'type': 'map'}
        self._collect_fields(group, owner, type_def, False)
        return type_def

    def _collect_fields(self, group, owner: str, type_def: Dict, optional: bool):
        for entry in _entries(group):
            if isinstance(entry, InlineGroup):
                # '? ( a, b )': the members of an optional group are optional
                self._collect_fields(entry.group, owner, type_def,
                                     optional or _is_optional(entry))
                continue
            if not isinstance(entry, Member) or entry.key is None:
                continue  # group references: not expanded yet (#72)
            field = self._field(entry, owner, optional)
            if field is not None:
                key, info = field
                type_def['fields'][key] = info
            elif entry.key.kind == 'type':
                # '* label => value': extra keys matching 'label' are allowed (#104)
                type_def.setdefault('computed_keys', []).append({
                    'key': self._text(Type((entry.key.type,)), f'{owner}@key'),
                    'type': self._text(entry.value, f'{owner}@value'),
                })

    def _field(self, member: Member, owner: str, optional: bool = False):
        """``(key, field-info)`` for a member with a fixed key, else ``None``."""
        key_node = member.key
        optional = optional or _is_optional(member)

        label = registered_label(member)
        if label is not None:
            key, name = _literal_key(label[1]), label[0]
        elif key_node.kind == 'bareword':
            key, name = key_node.name, member.comment or key_node.name
        else:
            # '1: T', '"x": T', and literal keys written '1 => T' (#104)
            literal = key_node.value if key_node.kind == 'value' else key_node.type
            key = _literal_key(literal)
            name = member.comment or (_spelling(literal) if key is not None else None)
        if key is None:
            return None
        info = {'name': name,
                'type': self._text(member.value, f'{owner}@{_path(key)}'),
                'optional': optional}
        if label is not None:
            info['registered'] = True
        return key, info

    def _array(self, group, owner: str) -> Dict:
        entries = _entries(group)
        fields: Dict[Any, Dict] = {}
        element_types: Dict[int, str] = {}
        for index, entry in enumerate(entries):
            if isinstance(entry, Member) and entry.key is not None:
                field = self._field(entry, owner)
                if field is not None:
                    fields[field[0]] = field[1]
                    element_types[index] = field[1]['type']
                    continue
                value = entry.value
            else:
                value = entry_type(_without_occurrence(entry))
                if value is None:
                    continue
            element_types[index] = self._text(value, f'{owner}@{index}')
        occurrence = ''
        if len(entries) == 1 and entries[0].occurrence is not None \
                and entries[0].occurrence.text in ('+', '*'):
            occurrence = entries[0].occurrence.text
        type_def = {'fields': fields, 'type': 'array',
                    'element_types': element_types, 'occurrence': occurrence}
        # '[ int, * tstr ]': elements past the last position repeat its type
        last = len(entries) - 1
        if last > 0 and last in element_types and entries[last].occurrence is not None \
                and entries[last].occurrence.max is None:
            type_def['repeat'] = last
        return type_def

    # --------------------------------------------------------------- text

    def _text(self, ty: Type, path: str) -> str:
        """Canonical text for *ty*, with inline maps replaced by synthetic names."""
        return format_node(self._lift(ty, path))

    def _lift(self, node, path: str):
        if isinstance(node, Type):
            alts = node.alternatives
            paths = [path] if len(alts) == 1 else [f'{path}@{i}' for i in range(len(alts))]
            lifted = []
            for alt, alt_path in zip(alts, paths):
                new = self._lift(alt, alt_path)
                if isinstance(new, Paren) and isinstance(alt, Name):
                    # a generic instance that is a choice ('opt<uint>' gives
                    # 'uint / nil'): its alternatives join this choice
                    lifted.extend(new.type.alternatives)
                else:
                    lifted.append(new)
            return Type(tuple(lifted))
        if isinstance(node, Map):
            return Name(self._synthetic(node, path))
        if isinstance(node, Name) and node.args:
            if _deeper_than(node, MAX_DEPTH):
                # arguments that grow at each level ('g<T> = { n: g<[[T]]> }')
                # are cut off before they are hashed or printed
                return Name('any')
            key = (node.name, node.args)
            known = self._instance_names.get(key)
            if known is not None:
                return Name(known)  # 'tree<T> = { ? l: tree<T> }' refers to itself
            if key in self._expanding or len(self._expanding) >= _MAX_INSTANCE_DEPTH:
                # a recursive choice ('list<T> = nil / [T, list<T>]'), or one
                # whose arguments keep changing ('g<T> = { n: g<[T]> }')
                return node
            self._expanding.add(key)
            try:
                expanded = self._expand(Type((node,)))
                structure = _structure(expanded, instance=True)
                if structure is not None:
                    # 'non-empty<{ ... }>', 'pair<int, tstr>': a structure of its own
                    return Name(self._synthetic(structure, path, key))
                if expanded.alternatives != (node,):
                    lifted = self._lift(expanded, path)
                    if len(lifted.alternatives) == 1:
                        return lifted.alternatives[0]
                    return Paren(lifted)
            finally:
                self._expanding.discard(key)
        if isinstance(node, Tag):
            inner = node.type.alternatives
            if len(inner) == 1 and isinstance(inner[0], (Map, Array)):
                name = self._synthetic(inner[0], f'{path}@tag')
                return Tag(node.tag, Type((Name(name),)))
            return Tag(node.tag, self._lift(node.type, path))
        if isinstance(node, Paren):
            return Paren(self._lift(node.type, path))
        if isinstance(node, Array):
            return Array(_map_group(node.group, lambda v, i: self._lift(v, f'{path}@{i}')))
        return node

    def _synthetic(self, structure, path: str, instance: Optional[tuple] = None) -> str:
        # The validator splits type text on '.' and whitespace: keep them out.
        path = re.sub(r'[^A-Za-z0-9_@$-]', '_', path)
        if path.endswith('-'):
            path += '_'  # a trailing '-' is not part of a CDDL name
        name = path
        n = 2
        while name in self.types or name in self._rule_names:
            name, n = f'{path}-{n}', n + 1
        self.synthetic_types.add(name)
        self.types[name] = {}  # reserve the name before recursing
        if instance is not None:
            self._instance_names[instance] = name
        self.types[name] = self._structure_def(structure, name)
        return name


# ------------------------------------------------------------------ helpers

def _deeper_than(node, limit: int) -> bool:
    """Whether *node* nests more than *limit* levels, checked without recursion."""
    stack = [(node, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > limit:
            return True
        for name in getattr(current, '__dataclass_fields__', ()):
            if name in ('span', 'comment'):
                continue
            child = getattr(current, name)
            for item in (child if isinstance(child, tuple) else (child,)):
                if hasattr(item, '__dataclass_fields__'):
                    stack.append((item, depth + 1))
    return False


def _is_optional(entry) -> bool:
    return entry.occurrence is not None and entry.occurrence.text in _OPTIONAL


def _entries(group):
    """All entries of all group choices, in source order."""
    return [entry for choice in group.choices for entry in choice.entries]


def _map_group(group, fn):
    """A copy of *group* with ``fn(value, index)`` applied to member values."""
    from .ast import Group, GroupChoice
    choices = []
    index = 0
    for choice in group.choices:
        entries = []
        for entry in choice.entries:
            if isinstance(entry, Member):
                entry = Member(entry.occurrence, entry.key, fn(entry.value, index),
                               entry.comment, span=entry.span)
            entries.append(entry)
            index += 1
        choices.append(GroupChoice(tuple(entries), span=choice.span))
    return Group(tuple(choices), span=group.span)


def _structure(ty, instance: bool = False):
    """The map or array a rule's type stands for, if it is one.

    ``x = { ... }`` and ``x = [ ... ]`` give that node. For an instantiated
    generic (*instance*), parentheses and controls around the structure are
    looked through, so ``non-empty<{ ... }>``, which instantiates to
    ``({ ... }) .and ({ + any => any })``, is read as its map (the control is
    not enforced). Without a resolved model, a single generic argument
    (``x = non-empty<{ ... }>``) is read as the map inside, as the
    line-based parser did.
    """
    if len(ty.alternatives) != 1:
        return None
    node = ty.alternatives[0]
    if isinstance(node, Name) and len(node.args) == 1:
        node = node.args[0]
    while instance:
        if isinstance(node, Control):
            node = node.target
        elif isinstance(node, Paren) and len(node.type.alternatives) == 1:
            node = node.type.alternatives[0]
        else:
            break
    return node if isinstance(node, (Map, Array)) else None


def _literal_key(node):
    """The CBOR map key a literal stands for, or ``None``."""
    if isinstance(node, (IntLit, TextLit)):
        return node.value
    return None


def _spelling(node) -> str:
    """How the legacy parser named a field with a literal key."""
    return node.value if isinstance(node, TextLit) else str(node.value)


def _path(key) -> str:
    return str(key)


def _without_occurrence(entry):
    if getattr(entry, 'occurrence', None) is None:
        return entry
    fields = {name: getattr(entry, name) for name in entry.__dataclass_fields__}
    fields['occurrence'] = None
    return type(entry)(**fields)
