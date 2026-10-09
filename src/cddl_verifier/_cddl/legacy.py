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

See ``docs/CDDL_AST_DESIGN.md`` §10.
"""

import re
from typing import Any, Dict, List, Optional

from .ast import (Array, GroupRule, InlineGroup, IntLit, Map, Member, Name, Paren, Tag,
                  TextLit, Type, TypeRule, entry_type, registered_label, walk)
from .printer import format_node

# Occurrences that make a map member optional.
_OPTIONAL = ('?', '*')


class LegacyTables:
    """The dictionaries ``CDDLParser`` exposes, built from a ``Schema``."""

    def __init__(self, schema):
        self.types: Dict[str, Dict] = {}
        self.groups: Dict[str, List[str]] = {}
        self.type_choices: Dict[str, List[str]] = {}
        self.socket_extensions: Dict[str, List[str]] = {}
        self.registered_params: Dict[Any, str] = {}
        self.type_aliases: Dict[str, str] = {}
        self.first_definition: Optional[str] = None
        #: Names given to inline maps and to structures inside tags (``r@a``).
        self.synthetic_types: set = set()
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
        if self.first_definition is None:
            self.first_definition = name

        structure = _structure(rule.type)
        alt = rule.type.alternatives[0]
        if rule.maybe_group and isinstance(alt, Paren):
            # 'g = ( name )' could be a group or a type; like the line-based
            # parser, treat it as a group until #70 resolves the name.
            self.groups[name] = [format_node(alt.type)]
        elif structure is not None:
            self.types[name] = self._structure_def(structure, name)
        else:
            self.type_aliases[name] = self._text(rule.type, name)

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
        return {'fields': fields, 'type': 'array',
                'element_types': element_types, 'occurrence': occurrence}

    # --------------------------------------------------------------- text

    def _text(self, ty: Type, path: str) -> str:
        """Canonical text for *ty*, with inline maps replaced by synthetic names."""
        return format_node(self._lift(ty, path))

    def _lift(self, node, path: str):
        if isinstance(node, Type):
            alts = node.alternatives
            if len(alts) == 1:
                return Type((self._lift(alts[0], path),))
            return Type(tuple(self._lift(a, f'{path}{i}') for i, a in enumerate(alts)))
        if isinstance(node, Map):
            return Name(self._synthetic(node, path))
        if isinstance(node, Name) and len(node.args) == 1 and isinstance(node.args[0], Map):
            # 'non-empty<{ ... }>': read the map inside, as for rules (_structure)
            return Name(self._synthetic(node.args[0], path))
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

    def _synthetic(self, structure, path: str) -> str:
        # The validator splits type text on '.' and whitespace: keep them out.
        path = re.sub(r'[^A-Za-z0-9_@$~-]', '_', path)
        name = path
        n = 2
        while name in self.types or name in self._rule_names:
            name, n = f'{path}~{n}', n + 1
        self.synthetic_types.add(name)
        self.types[name] = {}  # reserve the name before recursing
        self.types[name] = self._structure_def(structure, name)
        return name


# ------------------------------------------------------------------ helpers

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


def _structure(ty):
    """The map or array a rule's type stands for, if it is one.

    ``x = { ... }`` and ``x = [ ... ]`` give that node. So does a single
    generic argument (``x = non-empty<{ ... }>``): generics are not
    instantiated before #70, and the line-based parser also read the map
    inside them.
    """
    if len(ty.alternatives) != 1:
        return None
    node = ty.alternatives[0]
    if isinstance(node, Name) and len(node.args) == 1:
        node = node.args[0]
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
