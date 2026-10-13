"""Build the legacy ``CDDLParser`` tables from the AST (#110, Phase B of #69).

The validator and EDN generator still read the dictionaries the line-based
parser used to produce (``types``, ``type_aliases``, ``type_choices``,
``socket_extensions``, ``groups``, ``registered_params``,
``first_definition``). This module builds them from a parsed ``Schema`` so
those consumers keep working while they move to reading nodes (Phase C,
#111; #115). A type expression in a table is a ``Type`` node (#177); group
entries (``groups``, ``socket_extensions``) are still written with
:func:`format_node`.

Two things the validator cannot read from a type as written get a table entry instead:

* An inline map (``a: { b: uint }``), an inline array other than
  ``[ + T ]`` / ``[ * T ]`` (#133), or a map or array inside a tag
  (``#6.563([ value: bytes ])``), becomes a type of its own named
  ``<owner>@<path>`` (``r@a``), and the type refers to that name (#100).
* An array type has ``length``, the ``(min, max)`` number of elements its
  entries' occurrences allow (``max`` ``None`` for no limit), when it can be
  worked out (#133). An array whose element *i* does not always take entry
  *i* (``[ ? int, tstr ]``, ``[ int, pair ]``, ``[ a // b ]``) also has
  ``sequence``: its entries with their occurrences, which the validator
  matches the elements to (#135).
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

from .._cbor import map_key
from .ast import (Array, BytesLit, Control, FloatLit, Group, GroupRef, GroupRule, InlineGroup,
                  IntLit, Map, Member, Name, Paren, Tag, TextLit, Type, TypeRule, Unwrap,
                  entry_type, registered_label, walk)
from .parser import MAX_DEPTH
from .printer import format_node

#: How many generic instances may be expanding inside each other. Past it,
#: a reference is kept as it is, so 'g<T> = { n: g<[T]> }' terminates.
_MAX_INSTANCE_DEPTH = 16
#: Most ways a map's group choices and optional groups may combine (#72).
_MAX_MAP_ALTERNATIVES = 1024


class _TooManyAlternatives(Exception):
    """A map's group choices multiply past ``_MAX_MAP_ALTERNATIVES``."""
# Controls whose argument is a type that data is checked against (RFC 8610 §3.8)
_TYPE_ARG_CONTROLS = frozenset({'cbor', 'cborseq'})

# Occurrences that make a map member optional.
_OPTIONAL = ('?', '*')


class LegacyTables:
    """The dictionaries ``CDDLParser`` exposes, built from a ``Schema``."""

    def __init__(self, schema, resolved=None):
        #: ``ResolvedSchema`` (#70), used for rule kinds and generics.
        self.resolved = resolved
        self.types: Dict[str, Dict] = {}
        self.groups: Dict[str, List[str]] = {}
        self.type_choices: Dict[str, List[Type]] = {}
        self.socket_extensions: Dict[str, List[str]] = {}
        self.registered_params: Dict[Any, str] = {}
        self.type_aliases: Dict[str, Type] = {}
        self.first_definition: Optional[str] = None
        #: Names given to inline maps and to structures inside tags (``r@a``).
        self.synthetic_types: set = set()
        # Generic instances (name, args): the synthetic type built for each,
        # and those being expanded now, so recursive generics terminate.
        self._instance_names: Dict[tuple, str] = {}
        self._building: Dict[int, str] = {}
        self._computed = 0
        self._expanding: set = set()
        # Array lengths of group references (name, args), worked out once.
        self._ref_lengths: Dict[tuple, Optional[tuple]] = {}
        # Entry sequences of group references (name, args), built once.
        self._group_sequences: Dict[tuple, Optional[list]] = {}
        # Group references whose entry sequence is being built now (#138).
        self._sequences_building: set = set()
        self._rule_names = {rule.name for rule in schema.rules}

        # '/=' extends a rule, so the rules it extends are built first
        extensions = [r for r in schema.rules if isinstance(r, TypeRule) and r.assign == '/=']
        for rule in schema.rules:
            if isinstance(rule, TypeRule):
                if rule.assign != '/=':
                    self._type_rule(rule)
            else:
                self._group_rule(rule)
        for rule in extensions:
            self._type_rule(rule)

        # "IANA registered parameters" (&(name: N) => T) anywhere in the schema.
        for node in walk(schema):
            if isinstance(node, Member):
                label = registered_label(node)
                if label is not None:
                    key = _literal_key(label[1], self._rule_names)
                    if key is not NOT_A_KEY:
                        self.registered_params[key] = label[0]

    # ------------------------------------------------------------- rules

    def _type_rule(self, rule: TypeRule):
        name = rule.name
        if rule.assign == '/=':
            if name not in self.type_choices and not name.startswith('$'):
                # 'a = int' then 'a /= tstr': a is 'int / tstr', so the
                # original definition becomes the first alternative (#86)
                base = self._take_base(name)
                if base is not None:
                    self.type_choices[name] = [base]
            choices = self.type_choices.setdefault(name, [])
            for alt in rule.type.alternatives:
                choices.append(self._type_of(Type((alt,)), f'{name}@{len(choices)}'))
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
        instance = expanded is not ty
        structure = _structure(expanded, instance=instance)
        ty = expanded
        if structure is not None:
            self.types[name] = self._structure_def(structure, name)
            if instance:
                self._add_constraints(self.types[name], expanded, name)
        else:
            self.type_aliases[name] = self._type_of(ty, name)

    def _take_base(self, name: str) -> Optional[Type]:
        """Move the definition of rule *name* out of its own name, and return where it went."""
        if name in self.types:
            moved, n = f'{name}@base', 2
            while moved in self.types or moved in self._rule_names:
                moved, n = f'{name}@base-{n}', n + 1  # '@base' may be an inline map of the rule
            self.types[moved] = self.types.pop(name)
            self.synthetic_types.add(moved)
            return Type((Name(moved),))
        return self.type_aliases.pop(name, None)

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
        """The table entry of a map: its members, and group choices as alternatives.

        Group references, inline groups with their occurrences, group choices
        (``//``), group sockets and unwrapped maps (``~m``) are expanded into
        the alternatives a map can take (#72). Each alternative lists the
        members with fixed keys (``fields``) and those with a computed key
        (``computed_keys``, with the number of entries each takes). A map
        with one alternative is a plain ``fields`` table; with several it
        also has ``alternatives``, and ``fields`` holds the members of all
        of them, for naming only.
        """
        outer, self._computed = self._computed, 0
        try:
            return self._map_def(group, owner)
        finally:
            self._computed = outer

    def _map_def(self, group, owner: str) -> Dict:
        try:
            alternatives = self._group_partials(group, owner, ())
        except _TooManyAlternatives:
            # Matching fewer alternatives would accept or reject wrongly, so a
            # map like this is reported, not guessed at.
            return {'fields': {}, 'type': 'map', 'unsupported':
                    f"its group choices and optional groups combine into more than "
                    f"{_MAX_MAP_ALTERNATIVES} alternatives"}
        if alternatives is None:
            alternatives = [self._flat_partial(group, owner)]
        defs = [self._partial_def(fields, computed) for fields, computed in alternatives]
        if len(defs) == 1:
            return defs[0]
        union: Dict[Any, Dict] = {}
        for definition in defs:
            for key, info in definition['fields'].items():
                union.setdefault(key, info)
        for key, info in union.items():
            if not all(key in definition['fields'] for definition in defs):
                union[key] = dict(info, optional=True)  # not part of every alternative
        return {'fields': union, 'type': 'map', 'alternatives': defs}

    @staticmethod
    def _partial_def(fields: Dict, computed: List) -> Dict:
        type_def = {'fields': dict(fields), 'type': 'map'}
        if computed:
            type_def['computed_keys'] = [dict(c) for c in computed]
        return type_def

    def _flat_partial(self, group, owner: str):
        """Members written directly in *group*, when it cannot be expanded."""
        fields: Dict[Any, Dict] = {}
        computed: List[Dict] = []
        for entry in _entries(group):
            if isinstance(entry, Member):
                for more_fields, more_computed in self._member_partials(entry, owner):
                    fields.update(more_fields)
                    computed.extend(more_computed)
        return fields, computed

    def _group_partials(self, group, owner: str, expanding: tuple):
        """``[(fields, computed)]``, one per way *group* can match, or ``None``.

        ``None`` when an entry cannot be expanded (an undefined name, a type
        where a group is needed); the caller then keeps the members written
        directly in the map. Raises ``_TooManyAlternatives`` when the choices
        multiply past ``_MAX_MAP_ALTERNATIVES``.
        """
        result: List[tuple] = []
        for choice in group.choices:
            combined: List[tuple] = [({}, [])]
            for entry in choice.entries:
                options = self._entry_partials(entry, owner, expanding)
                if options is None:
                    return None
                if len(combined) * len(options) > _MAX_MAP_ALTERNATIVES:
                    raise _TooManyAlternatives
                combined = [_merge_partials(left, right)
                            for left in combined for right in options]
            result.extend(combined)
            if len(result) > _MAX_MAP_ALTERNATIVES:
                raise _TooManyAlternatives
        return result

    def _entry_partials(self, entry, owner: str, expanding: tuple):
        if isinstance(entry, Member):
            if entry.key is not None:
                return self._member_partials(entry, owner)
            # '~hdr' in a map splices the members of the map 'hdr'
            splice = _spliced(entry)
            if splice is not None:
                return self._unwrap_partials(splice, entry, owner, expanding)
            return [({}, [])]
        if isinstance(entry, InlineGroup):
            partials = self._group_partials(entry.group, owner, expanding)
            return None if partials is None else _occurs(partials, entry.occurrence)
        if not isinstance(entry, GroupRef) or self.resolved is None:
            return None
        if entry.name in self.resolved.external:
            return _occurs([self._unknown_group()], entry.occurrence)
        if self.resolved.kind(entry.name) != 'group':
            return None
        key = (entry.name, entry.args)
        if key in expanding:
            return [({}, [])]  # a group that refers to itself adds no new members
        try:
            group = self.resolved.instantiate(entry.name, entry.args)
        except ValueError:
            return None
        if not isinstance(group, Group):
            return None
        partials = self._group_partials(group, f'{owner}', expanding + (key,))
        return None if partials is None else _occurs(partials, entry.occurrence)

    @staticmethod
    def _unknown_group():
        """What a group from a module that was not loaded may add: any entries (#121)."""
        return {}, [{'key': 'any', 'type': 'any', 'min': 0, 'max': None}]

    def _unwrap_partials(self, node: Unwrap, entry, owner: str, expanding: tuple):
        if self.resolved is not None and node.name in self.resolved.external:
            return _occurs([self._unknown_group()], entry.occurrence)
        target = self._unwrap_target(node)
        if not isinstance(target, Map):
            return None
        key = ('~' + node.name, node.args)
        if key in expanding:
            return [({}, [])]
        partials = self._group_partials(target.group, owner, expanding + (key,))
        return None if partials is None else _occurs(partials, entry.occurrence)

    def _unwrapped_tag_type(self, node: Unwrap):
        """The type inside the tag ``~name`` unwraps, or ``None`` if it is not a tag."""
        if self.resolved is None:
            return None
        name, args = node.name, node.args
        for _ in range(_MAX_INSTANCE_DEPTH):
            try:
                ty = self.resolved.instantiate(name, args)
            except ValueError:
                return None
            if not isinstance(ty, Type) or len(ty.alternatives) != 1:
                return None
            head = ty.alternatives[0]
            while isinstance(head, Paren) and len(head.type.alternatives) == 1:
                head = head.type.alternatives[0]
            if isinstance(head, Tag):
                return head.type
            if isinstance(head, Name) and self.resolved.kind(head.name) == 'type':
                name, args = head.name, head.args
                continue
            return None
        return None

    def _unwrap_target(self, node: Unwrap):
        """The map, array or other node ``~name`` stands for, tags looked through."""
        if self.resolved is None:
            return None
        name, args = node.name, node.args
        for _ in range(_MAX_INSTANCE_DEPTH):
            try:
                ty = self.resolved.instantiate(name, args)
            except ValueError:
                return None
            if not isinstance(ty, Type) or len(ty.alternatives) != 1:
                return None
            head = ty.alternatives[0]
            while isinstance(head, Paren) and len(head.type.alternatives) == 1:
                head = head.type.alternatives[0]
            if isinstance(head, Tag):
                inner = head.type
                if len(inner.alternatives) != 1:
                    return None
                head = inner.alternatives[0]
                while isinstance(head, Paren) and len(head.type.alternatives) == 1:
                    head = head.type.alternatives[0]
            if isinstance(head, Name) and self.resolved.kind(head.name) == 'type':
                name, args = head.name, head.args
                continue
            return head
        return None

    def _member_partials(self, member: Member, owner: str):
        """The partial for a member with a key: a fixed field or a computed key."""
        field = self._field(member, owner)
        if field is not None:
            key, info = field
            return [({key: info}, [])]
        if member.key is not None and member.key.kind == 'type':
            # '* label => value': extra keys matching 'label' are allowed (#104).
            # The occurrence says how many entries match (#120, #151): none
            # means exactly one, '?' at most one, '+' at least one.
            occurrence = member.occurrence
            low, high = (1, 1) if occurrence is None else (occurrence.min, occurrence.max)
            self._computed += 1
            computed = {
                'key': self._type_of(Type((member.key.type,)), f'{owner}@key{self._computed}'),
                'type': self._type_of(member.value, f'{owner}@value{self._computed}'),
                'min': low, 'max': high,
            }
            if member.key.cut:
                computed['cut'] = True
            return [({}, [computed])]
        return [({}, [])]

    def _field(self, member: Member, owner: str, optional: bool = False):
        """``(key, field-info)`` for a member with a fixed key, else ``None``."""
        key_node = member.key
        optional = optional or _is_optional(member)

        label = registered_label(member)
        if label is not None:
            key, name = _literal_key(label[1], self._rule_names), label[0]
        elif key_node.kind == 'bareword':
            key, name = key_node.name, member.comment or key_node.name
        else:
            # '1: T', '"x": T', and literal keys written '1 => T' (#104)
            literal = key_node.value if key_node.kind == 'value' else key_node.type
            key = _literal_key(literal, self._rule_names)
            name = member.comment or (_spelling(literal) if key is not NOT_A_KEY else None)
        if key is NOT_A_KEY:
            return None
        info = {'name': name,
                'type': self._type_of(member.value, f'{owner}@{_path(key)}'),
                'optional': optional}
        if label is not None:
            info['registered'] = True
        return key, info

    def _array(self, group, owner: str) -> Dict:
        entries = _entries(group)
        fields: Dict[Any, Dict] = {}
        element_types: Dict[int, Type] = {}
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
            element_types[index] = self._type_of(value, f'{owner}@{index}')
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
        if len(group.choices) > 1:
            # '[ int // tstr ]': an index does not name one entry across the
            # choices, so only the number of elements is checked (#135)
            type_def['element_types'] = {}
            type_def.pop('repeat', None)
        length = self._group_length(group, ())
        if length is not None:
            type_def['length'] = length
        if not _positional(group, entries, element_types, self.resolved):
            sequence = self._sequence(group, owner, (), element_types)
            if sequence is not None:
                type_def['sequence'] = sequence
        return type_def

    def _sequence(self, group, path: str, expanding: tuple,
                  texts: Optional[Dict[int, Type]] = None):
        """*group* as entry sequences an array's elements are matched to (#135).

        One list per group choice; each entry is ``{'min', 'max', 'type'}``
        for one element of type text ``type``, or ``{'min', 'max', 'group'}``
        for a nested group (a group name or an inline group) in the same
        form. ``max`` is ``None`` for no limit. A group that refers to itself
        is entered as ``{'min', 'max', 'ref'}``, with the ``(name, args)`` key
        of the group, which the matcher looks up with :meth:`group_sequence`
        while matching, so the recursion is followed as deep as the elements
        go (#138). *texts* are the type texts already written for the
        top-level entries, by index. ``None`` when an entry cannot be
        expanded: an undefined name, a socket or a generic parameter.
        """
        choices = []
        index = 0
        for c, choice in enumerate(group.choices):
            entries = []
            for entry in choice.entries:
                unit = self._sequence_unit(entry, f'{path}@{index}', expanding,
                                           texts.get(index) if texts is not None else None)
                index += 1
                if unit is None:
                    return None
                occurrence = entry.occurrence
                unit['min'] = 1 if occurrence is None else occurrence.min
                unit['max'] = 1 if occurrence is None else occurrence.max
                entries.append(unit)
            choices.append(entries)
        return choices

    def _sequence_unit(self, entry, path: str, expanding: tuple, text: Optional[Type]):
        if isinstance(entry, Member) and _spliced(entry) is not None:
            # '~hdr': the entries of the array 'hdr', in place (#158)
            splice = _spliced(entry)
            target = self._unwrap_target(splice)
            key = ('~' + splice.name, splice.args)
            if not isinstance(target, (Array, Map)) or key in expanding:
                return None
            group = self._sequence(target.group, path, expanding + (key,))
            return None if group is None else {'group': group}
        if isinstance(entry, Member):
            if text is None:
                text = self._type_of(entry.value, path)
            return {'type': text}
        if isinstance(entry, InlineGroup):
            group = self._sequence(entry.group, path, expanding)
            return None if group is None else {'group': group}
        if not isinstance(entry, GroupRef) or self.resolved is None \
                or entry.name.startswith('$'):
            return None
        kind = self.resolved.kind(entry.name)
        key = (entry.name, entry.args)
        if kind == 'group' and key in self._sequences_building:
            return {'ref': key}  # a group that refers to itself, directly or not
        if entry.name in expanding:
            return None  # the same group with other arguments
        if kind == 'type':
            if text is None:
                text = self._type_of(Type((Name(entry.name, entry.args),)), path)
            return {'type': text}
        if kind != 'group':
            return None
        if key not in self._group_sequences:
            self._build_group_sequence(key, expanding)
        sequence = self._group_sequences[key]
        return None if sequence is None else {'group': sequence}

    def _build_group_sequence(self, key: tuple, expanding: tuple):
        """Work out the entry sequence of the group *key* and keep it."""
        known = set(self._group_sequences)
        self._sequences_building.add(key)
        try:
            try:
                group = self.resolved.instantiate(key[0], key[1])
            except ValueError:
                group = None
            sequence = None if not isinstance(group, Group) else \
                self._sequence(group, re.sub(r'\W', '_', key[0]), expanding + (key[0],))
        finally:
            self._sequences_building.discard(key)
        self._group_sequences[key] = sequence
        if sequence is None:
            # groups built meanwhile may refer to this one, which has no sequence
            for other in set(self._group_sequences) - known - {key}:
                del self._group_sequences[other]

    def group_sequence(self, key: tuple) -> Optional[list]:
        """The entry sequence of the group *key* (``(name, args)``), if it has one (#138)."""
        return self._group_sequences.get(key)

    def _group_length(self, group, expanding: tuple):
        """``(min, max)`` elements an array with *group* holds, or ``None``.

        ``max`` is ``None`` when there is no upper bound. Group choices give
        the widest bounds of their alternatives. ``None`` means the bounds are
        unknown: a reference to an undefined name, a socket or a generic
        parameter, or a group that refers to itself.
        """
        bounds = []
        for choice in group.choices:
            low, high = 0, 0
            for entry in choice.entries:
                length = self._entry_length(entry, expanding)
                if length is None:
                    return None
                low += length[0]
                high = None if high is None or length[1] is None else high + length[1]
            bounds.append((low, high))
        low = min(b[0] for b in bounds)
        high = None if any(b[1] is None for b in bounds) else max(b[1] for b in bounds)
        return low, high

    def _entry_length(self, entry, expanding: tuple):
        if isinstance(entry, Member):
            inner = (1, 1)
            splice = _spliced(entry)
            if splice is not None:
                # '~hdr' in an array stands for the entries of the array 'hdr' (#158)
                target = self._unwrap_target(splice)
                key = ('~' + splice.name, splice.args)
                if not isinstance(target, (Array, Map)) or key in expanding:
                    return None
                inner = self._group_length(target.group, expanding + (key,))
        elif isinstance(entry, InlineGroup):
            inner = self._group_length(entry.group, expanding)
        elif isinstance(entry, GroupRef):
            inner = self._ref_length(entry, expanding)
        else:
            inner = None
        if inner is None:
            return None
        occurrence = entry.occurrence
        if occurrence is None:
            return inner
        low = inner[0] * occurrence.min
        if inner[1] == 0 or occurrence.max == 0:
            return low, 0
        if inner[1] is None or occurrence.max is None:
            return low, None
        return low, inner[1] * occurrence.max

    def _ref_length(self, ref: GroupRef, expanding: tuple):
        """Elements a bare name in an array stands for: one for a type."""
        if self.resolved is None or ref.name.startswith('$') or ref.name in expanding:
            return None
        kind = self.resolved.kind(ref.name)
        if kind == 'type':
            return 1, 1
        if kind != 'group':
            return None
        try:
            group = self.resolved.instantiate(ref.name, ref.args)
        except ValueError:
            return None
        if not isinstance(group, Group):
            return None
        key = (ref.name, ref.args)
        if key not in self._ref_lengths:
            self._ref_lengths[key] = self._group_length(group, expanding + (ref.name,))
        return self._ref_lengths[key]

    # --------------------------------------------------------------- types

    def _type_of(self, ty: Type, path: str) -> Type:
        """*ty* with inline maps, arrays and tags replaced by synthetic names."""
        return self._lift(ty, path)

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
        if isinstance(node, Unwrap) and not node.args:
            # '~t' with 't = #6.5(int)' is the type inside the tag, 'int' (#158)
            inner = self._unwrapped_tag_type(node)
            if inner is not None:
                lifted = self._lift(inner, path)
                return lifted.alternatives[0] if len(lifted.alternatives) == 1 else Paren(lifted)
            return node
        if isinstance(node, Name) and self.resolved is not None \
                and node.name in self.resolved.external:
            return Name('any')  # from a module that was not loaded (#121)
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
                    synthetic = self._synthetic(structure, path, key)
                    self._add_constraints(self.types[synthetic], expanded, synthetic)
                    return Name(synthetic)
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
        if isinstance(node, Control):
            # 'opt<uint> .size 1' gives '(uint / nil) .size 1'
            arg = node.arg
            if node.op in _TYPE_ARG_CONTROLS:
                # 'bstr .cbor pair<uint, tstr>', 'bstr .cbor { a: uint }': the
                # embedded item is checked against the argument (#127)
                if isinstance(arg, Array):
                    # '[+ p]' too: the embedded item is checked as a rule of its own
                    arg = Name(self._synthetic(arg, f'{path}@arg'))
                else:
                    arg = _as_type2(self._lift(Type((arg,)), f'{path}@arg'))
            elif node.op in _AND_CONTROLS:
                # 'T .and { a: 0..5 }': the value must match the operand too, so a
                # map or array there is a structure of its own, as around a generic
                inner = arg
                while isinstance(inner, Paren) and len(inner.type.alternatives) == 1:
                    inner = inner.type.alternatives[0]
                if isinstance(inner, (Map, Array)):
                    arg = Name(self._synthetic(inner, f'{path}@{node.op}'))
                else:
                    arg = _as_type2(self._lift(Type((arg,)), f'{path}@{node.op}'))
            target = node.target
            if node.op in _AND_CONTROLS:
                # '[+ uint] .and [* int]': a structure target is lifted too, or its
                # own shape would be lost behind the control
                inner = target
                while isinstance(inner, Paren) and len(inner.type.alternatives) == 1:
                    inner = inner.type.alternatives[0]
                if isinstance(inner, (Map, Array)):
                    target = Name(self._synthetic(inner, f'{path}@target'))
            return Control(self._lift(target, path), node.op, arg)
        if isinstance(node, Array):
            if not self._uniform(node):
                # '[ int, tstr ]': a structure of its own, so its elements are
                # counted and checked by position (#133)
                return Name(self._synthetic(node, path))
            return Array(_canonical_entries(
                _map_group(node.group, lambda v, i: self._lift(v, f'{path}@{i}'))))
        return node

    def _uniform(self, array: Array) -> bool:
        """Whether *array* is ``[ + T ]`` or ``[ * T ]``, which stays inline.

        ``T`` must stand for one element: ``[ + g ]`` with a group ``g`` of
        several entries is a structure of its own, so its length is checked.
        """
        entries = _entries(array.group)
        if len(entries) != 1 or entries[0].occurrence is None \
                or entries[0].occurrence.text not in ('+', '*'):
            return False
        entry = entries[0]
        if isinstance(entry, GroupRef):
            return self._ref_length(GroupRef(None, entry.name, entry.args), ()) == (1, 1)
        return isinstance(entry, Member)

    def _synthetic(self, structure, path: str, instance: Optional[tuple] = None) -> str:
        if instance is None and isinstance(structure, Map) and id(structure) in self._building:
            return self._building[id(structure)]
        # A name is never cut at '.' or whitespace: keep them out.
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
        # A map that reaches itself through a group it uses ('g = (n: {g})')
        # names the structure being built instead of building it again.
        self._building[id(structure)] = name
        try:
            self.types[name] = self._structure_def(structure, name)
        finally:
            self._building.pop(id(structure), None)
        return name

    def _add_constraints(self, type_def: Dict, expanded: Type, owner: str):
        """Record the ``.and`` / ``.within`` operands around an instantiated structure.

        ``non-empty<M>`` instantiates to ``(M) .and ({ + any => any })``. The
        table describes ``M``; the other operand is kept in
        ``type_def['constraints']`` as a type, and a value must match it
        as well (#120).
        """
        operands = _control_operands(expanded)
        if not operands:
            return
        constraints = []
        for index, (op, operand) in enumerate(operands):
            path = f'{owner}@{op}{index}'
            inner = operand
            while isinstance(inner, Paren) and len(inner.type.alternatives) == 1:
                inner = inner.type.alternatives[0]
            if isinstance(inner, Array):
                # '.and ([ + int ])': a structure of its own, so its length is checked
                constraints.append(Type((Name(self._synthetic(inner, path)),)))
            else:
                constraints.append(self._type_of(Type((operand,)), path))
        type_def['constraints'] = constraints


# ------------------------------------------------------------------ helpers

# Controls that restrict a structure by a second type the value must also match.
_AND_CONTROLS = ('and', 'within')


def _control_operands(ty):
    """The ``(control, operand)`` pairs :func:`_structure` looks through.

    Only ``.and`` and ``.within`` are collected: both need the value to match
    the operand too. Other controls (``.size``, ...) are still ignored there.
    """
    operands = []
    node = ty.alternatives[0] if len(ty.alternatives) == 1 else None
    if isinstance(node, Name) and len(node.args) == 1:
        node = node.args[0]
    while True:
        if isinstance(node, Control):
            if node.op in _AND_CONTROLS:
                operands.append((node.op, node.arg))
            node = node.target
        elif isinstance(node, Paren) and len(node.type.alternatives) == 1:
            node = node.type.alternatives[0]
        else:
            break
    operands.reverse()
    return operands

def _positional(group, entries, element_types, resolved) -> bool:
    """Whether element *i* of an array always takes entry *i* (``element_types``).

    True for one group choice of single-element entries where only the last
    one has an occurrence (``[ int, tstr ]``, ``[ int, * tstr ]``,
    ``[ 2*3 int ]``). Other arrays need their elements matched to the entries
    (``[ ? int, tstr ]``, ``[ int, pair ]``), see ``sequence``.
    """
    if len(group.choices) != 1:
        return False
    last = len(entries) - 1
    for index, entry in enumerate(entries):
        if index not in element_types:
            return False
        if isinstance(entry, GroupRef) and (resolved is None or resolved.kind(entry.name) != 'type'):
            return False
        if not isinstance(entry, (Member, GroupRef)):
            return False
        if isinstance(entry, Member) and _spliced(entry) is not None:
            return False
        occurrence = entry.occurrence
        if occurrence is None:
            continue
        if index != last:
            return False
        # the last entry repeats its type ('repeat'), or occurs at most once
        if last > 0 and occurrence.max is not None and occurrence.max > 1:
            return False
    return True


def _as_type2(ty: Type):
    """*ty* as a single type2: its only alternative, or a parenthesized choice."""
    return ty.alternatives[0] if len(ty.alternatives) == 1 else Paren(ty)


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


def _canonical_entries(group):
    """*group* with a keyless member that is just a name written as the parser reads it.

    ``[ + evs@series@0 ]`` parses with a ``GroupRef`` entry; lifting a map out
    of ``[ + { ... } ]`` leaves a ``Member`` whose value is the new name. They
    print the same, so make the node the one the text parses to, so that types that print alike are equal (#175).
    """
    from .ast import Group, GroupChoice
    choices = []
    for choice in group.choices:
        entries = []
        for entry in choice.entries:
            if isinstance(entry, Member) and entry.key is None \
                    and len(entry.value.alternatives) == 1 \
                    and isinstance(entry.value.alternatives[0], Name):
                name = entry.value.alternatives[0]
                entry = GroupRef(entry.occurrence, name.name, name.args,
                                 entry.comment, span=entry.span)
            entries.append(entry)
        choices.append(GroupChoice(tuple(entries), span=choice.span))
    return Group(tuple(choices), span=group.span)


def _structure(ty, instance: bool = False):
    """The map or array a rule's type stands for, if it is one.

    ``x = { ... }`` and ``x = [ ... ]`` give that node. For an instantiated
    generic (*instance*), parentheses and controls around the structure are
    looked through, so ``non-empty<{ ... }>``, which instantiates to
    ``({ ... }) .and ({ + any => any })``, is read as its map (``.and`` and
    ``.within`` operands are kept as constraints, see ``_add_constraints``). Without a resolved model, a single generic argument
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


def _spliced(entry):
    """The ``Unwrap`` of a group entry that is just ``~name``, else ``None``."""
    if isinstance(entry, Member) and entry.key is None:
        alternatives = entry.value.alternatives
        if len(alternatives) == 1 and isinstance(alternatives[0], Unwrap):
            return alternatives[0]
    return None


def _merge_partials(left, right):
    """The members of two partial maps together; a key written twice keeps the first."""
    fields = dict(left[0])
    for key, info in right[0].items():
        fields.setdefault(key, info)
    return fields, left[1] + right[1]


def _occurs(partials, occurrence):
    """*partials* of a group used *occurrence* times (``None`` is once).

    Optional (``?``, ``*``) adds the way of taking none. A group used more
    than once multiplies how many entries its computed-key members take.
    """
    if occurrence is None:
        return partials
    low, high = occurrence.min, occurrence.max
    result = []
    for fields, computed in partials:
        if high is None or high > 1 or low > 1:
            computed = [dict(c, min=c['min'] * low,
                             max=None if c['max'] is None or high is None else c['max'] * high)
                        for c in computed]
        result.append((fields, computed))
    if low == 0:
        result.append(({}, []))
    return result


#: What :func:`_literal_key` returns for a key that is not a literal. It is not
#: ``None``, which is the dict key of a CBOR ``null`` key (#160).
NOT_A_KEY = object()


def _literal_key(node, rule_names=()):
    """The CBOR map key a literal stands for, or :data:`NOT_A_KEY`.

    The key is the dict key the decoder gives the same CBOR value
    (:func:`~cddl_verifier._cbor.map_key`): ``1.5`` is ``CBORKey(1.5)``, a
    byte string is ``bytes``, and ``true``/``false`` written as a type
    (``true => int``; ``true: int`` is the text key ``"true"``) are
    ``CBORKey(True)``/``CBORKey(False)``, and ``null``/``nil`` are ``None``,
    unless the schema defines those names itself (#142, #160).
    """
    if isinstance(node, (IntLit, TextLit)):
        return node.value
    if isinstance(node, (FloatLit, BytesLit)):
        return map_key(node.value)
    if isinstance(node, Name) and not node.args and node.name in ('true', 'false') \
            and node.name not in rule_names:
        return map_key(node.name == 'true')
    if isinstance(node, Name) and not node.args and node.name in ('null', 'nil') \
            and node.name not in rule_names:
        return None
    return NOT_A_KEY


def _spelling(node) -> str:
    """How the legacy parser named a field with a literal key."""
    if isinstance(node, TextLit):
        return node.value
    if isinstance(node, Name):
        return node.name
    return node.text if isinstance(node, (FloatLit, BytesLit)) else str(node.value)


def _path(key) -> str:
    return str(key)


def _without_occurrence(entry):
    if getattr(entry, 'occurrence', None) is None:
        return entry
    fields = {name: getattr(entry, name) for name in entry.__dataclass_fields__}
    fields['occurrence'] = None
    return type(entry)(**fields)
