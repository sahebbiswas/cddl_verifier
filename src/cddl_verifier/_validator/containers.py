"""Arrays, maps, groups and repeated choices.

Moved verbatim out of ``_analyzer`` (#83); ``CBORAnalyzer`` composes it.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .._cbor import CBORKey
from .._cddl import query
from .._cddl.ast import GroupRef, InlineGroup, Member, Tag, Type
from .._cddl.printer import format_node
from .._cddl.typeexpr import as_type, bare_name
from .._diagnostics import _Index
from .._log import Colors, logger
from .support import _CheckError, _describe_value, _entries_wanted, _UNKNOWN_TYPE


class ContainersMixin:
    """Arrays, maps, groups and repeated choices."""

    def _match_array(self, data: Sequence, sequence: List, type_name: str) -> Optional[str]:
        """Match the elements of *data* to the entry *sequence* of an array.

        Each entry takes between its ``min`` and ``max`` elements in order,
        and a nested group takes one of its choices (RFC 8610 §3.4). Every
        assignment is followed at once, as the set of positions it can reach
        after each entry, so each element is checked against each entry type
        at most once. Returns ``None`` when one assignment takes every
        element, else an error naming the first element none gets past.

        A position is reached with the ``.feature`` names (RFC 9165) used by the elements
        that were taken on the way there, so that when an assignment is found only the
        features of the elements it gave to entries are recorded, not those of an entry
        that an element was tried against and then went to another.
        """
        count = len(data)
        fits: Dict[Tuple[int, Type], bool] = {}
        fit_features: Dict[Tuple[int, Type], Tuple[str, ...]] = {}
        tried: Dict[int, List[Tuple[Type, str]]] = {}
        furthest = [0]
        # Ends found so far for a group reference being followed, by (reference,
        # positions), and those of them that were entered again meanwhile.
        approximate: Dict[tuple, set] = {}
        reentered: set = set()

        # The functions below are generators that hand the group they need
        # next to ``solve`` instead of calling it, so a group that refers to
        # itself is followed one element at a time on a stack of its own and
        # not on the Python call stack: the length of the array is not limited
        # by the recursion limit (#138, #162).
        def unit_ends(entry, starts):
            if 'group' in entry:
                return (yield group_ends(entry['group'], starts))
            if 'ref' in entry:
                # A group that refers to itself: follow it from these positions.
                # Reaching it again from the same positions, before any element
                # is taken (``g = (? g, int)``), gives what is known so far, and
                # the outer visit is repeated until nothing new is found, so the
                # matches that take elements after the inner one are kept and
                # a recursion that takes none ends (a least fixed point).
                visit = (entry['ref'], frozenset(starts))
                choices = self.cddl.group_sequence(entry['ref'])
                if choices is None:
                    return {}
                if visit in approximate:
                    reentered.add(visit)
                    return dict(approximate[visit])
                approximate[visit] = {}
                try:
                    while True:
                        ends = yield group_ends(choices, starts)
                        if visit not in reentered or ends.keys() <= approximate[visit].keys():
                            return ends
                        approximate[visit] = ends
                        reentered.discard(visit)
                finally:
                    del approximate[visit]
                    reentered.discard(visit)
            node = as_type(entry['type'])
            ends: Dict[int, Tuple[str, ...]] = {}
            for pos, used in starts.items():
                if pos >= count:
                    continue
                key = (pos, node)
                if key not in fits:
                    self._push_breadcrumb(f"[{pos}]", step=_Index(pos))  # where the element is
                    mark = len(self._feature_trail)
                    try:
                        errors = self._check_value(data[pos], node)
                    finally:
                        self._pop_breadcrumb()
                    fits[key] = not errors
                    # the element may yet go to another entry: keep what it used with the fit
                    fit_features[key] = tuple(self._feature_trail[mark:])
                    del self._feature_trail[mark:]
                    if errors:
                        tried.setdefault(pos, []).append((node, errors[0]))
                if fits[key]:
                    ends.setdefault(pos + 1, used + fit_features[key])
                    furthest[0] = max(furthest[0], pos + 1)
            return ends

        def entry_ends(entry, starts):
            low, high = entry['min'], entry['max']
            ends = dict(starts) if low == 0 else {}
            seen = set(ends)
            frontier, done = dict(starts), 0
            while frontier and (high is None or done < high):
                done += 1
                reached = yield unit_ends(entry, frontier)
                if done >= low:
                    # a position reached again, after more repeats, adds nothing
                    reached = {pos: used for pos, used in reached.items() if pos not in seen}
                    seen |= reached.keys()
                    ends.update(reached)
                frontier = reached
            return ends

        def group_ends(choices, starts):
            ends: Dict[int, Tuple[str, ...]] = {}
            for entries in choices:
                reached = dict(starts)
                for entry in entries:
                    reached = yield entry_ends(entry, reached)
                    if not reached:
                        break
                for pos, used in reached.items():
                    ends.setdefault(pos, used)
            return ends

        def solve(task):
            """Run *task*, a generator that yields the generators it needs."""
            stack, value = [task], None
            while stack:
                try:
                    needed = stack[-1].send(value)
                except StopIteration as done:
                    stack.pop()
                    value = done.value
                else:
                    stack.append(needed)
                    value = None
            return value

        taken = solve(group_ends(sequence, {0: ()}))
        if count in taken:
            self._feature_trail.extend(taken[count])  # the features of the elements as assigned
            return None
        index = furthest[0]
        if index >= count:
            return (f"Array type '{type_name}' is missing element [{count}]: "
                    f"the array ends before its required entries")
        if index in tried:
            reasons = '; '.join(f"{format_node(node)}: {error}" for node, error in tried[index][:3])
            message = f"Array element [{index}] of '{type_name}' matches no entry ({reasons})"
            # When one entry failed inside the element (an array or map in it),
            # the finding is at that place; otherwise at the element
            deeper = [error for _, error in tried[index]
                      if isinstance(error, _CheckError) and error.path]
            if len(deeper) == 1:
                found = deeper[0]
                return _CheckError(message, expected=found.expected, actual=found.actual,
                                   category=found.category, path=(_Index(index),) + found.path,
                                   schema_path=found.schema_path, rule=found.rule,
                                   span=found.span, located=found.located)
            return _CheckError(message, path=(_Index(index),))
        return _CheckError(f"Array type '{type_name}' has unexpected element [{index}]",
                           path=(_Index(index),))

    def _sandboxed_validate(self, value: Any, type_def: Dict, type_name: str) -> List[_CheckError]:
        """Run :meth:`_validate_type` and return its errors without keeping them.

        The errors keep what the run found out about each: the path below the
        current item, the rules entered, expected and actual, and where it sits.
        """
        saved_errors, saved_breadcrumb = self.validation_errors, list(self.breadcrumb)
        saved_path, saved_rules = list(self._data_path), list(self._rules)
        prefix, depth = self._current_path(), len(saved_breadcrumb)
        mark = len(self._feature_trail)
        self.validation_errors = []
        try:
            self._validate_type(value, type_def, type_name)
            found = self.validation_errors
        finally:
            self.validation_errors, self.breadcrumb = saved_errors, saved_breadcrumb
            self._data_path, self._rules = saved_path, saved_rules
        if found:
            del self._feature_trail[mark:]  # a part that did not match used no feature
        return [self._relative_error(error, prefix, depth) for error in found]

    @staticmethod
    def _relative_error(error: str, prefix: Tuple[Any, ...], depth: int) -> _CheckError:
        """*error*, a recorded finding, as an error below the item at *prefix*."""
        diag = getattr(error, 'diagnostic', None)
        if diag is None:
            return _CheckError(str(error))
        below = diag.path[len(prefix):] if diag.path[:len(prefix)] == prefix else diag.path
        span = None if diag.offset is None else (diag.offset, diag.end_offset)
        return _CheckError(str(error), expected=diag.expected, actual=diag.actual,
                           category=diag.category, path=below,
                           schema_path=diag.schema_path[depth:], rule=diag.rule,
                           span=span, located=True, alternatives=diag.alternatives)

    def _is_structured(self, alt) -> bool:
        """Whether choice alternative *alt* is a map, array or tag, by name or in full."""
        resolved = self.cddl.resolve_alias(alt)
        item = query.item_ref(resolved)
        name = bare_name(resolved)
        return ((name is not None and name in self.cddl.types)
                or query.tag(resolved) is not None
                or query.is_array(resolved)
                or (item is not None and (isinstance(item.node, Tag)
                                          or getattr(item.node, 'major', 0) in (4, 5, 6))))

    def _alternative_text(self, alt) -> str:
        """*alt* as written, or its kind (``map``, ``array``) for a structure written inline."""
        text = format_node(alt)
        name = bare_name(as_type(alt))
        if '@' in text and name is not None and name in self.cddl.types:
            return self.cddl.types[name].get('type') or text  # a lifted 'r@0@2'
        return text

    def _deferred_keys(self, data: dict, type_def: dict) -> set:
        """Keys of optional members written ``K => T`` that a later member may take (#202).

        An entry whose value is not a ``T`` stays free when some computed-key member
        (``* K => V``) accepts both its key and its value; otherwise it is the
        ``K => T`` member's, and its mismatch is reported there.
        """
        computed_keys = type_def.get('computed_keys')
        if not computed_keys:
            return set()
        deferred = set()
        mark = len(self._feature_trail)
        for key, info in type_def['fields'].items():
            if key not in data or info.get('cut', True) or not info.get('optional'):
                continue
            if not self._check_value(data[key], info.get('type') or _UNKNOWN_TYPE):
                continue  # it matches: the member takes it
            key_value = key.value if isinstance(key, CBORKey) else key
            if any(not self._check_value(key_value, c['key'], strict=True)
                   and not self._check_value(data[key], c['type']) for c in computed_keys):
                deferred.add(key)
        del self._feature_trail[mark:]  # probes: the member that takes the entry is checked later
        return deferred

    def _check_repeated_choices(self, data: dict, type_def: dict, type_name: str, breadcrumb,
                                counts: dict) -> None:
        """A group choice repeated in a map (``{+ g}``): each repetition took one alternative (#201).

        The members of all the alternatives were checked as optional ones, and the
        entries of wildcard members counted in *counts* (by position in
        ``computed_keys``). Here each alternative says how many repetitions it can
        stand for: an alternative with fixed keys is present when an entry of it
        matches, takes its other required members, and is used once (a key cannot
        repeat); one with only wildcard members is used as often as its entries
        need (each repetition takes the member's own minimum to maximum), and when
        its members are all optional as often as the choice allows. The numbers
        of repetitions the alternatives can stand for together have to meet the
        occurrence of the choice.
        """
        unbounded = float('inf')
        computed = type_def.get('computed_keys', ())

        def complain(error_msg, *args, **kwargs):
            logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
            self._report(error_msg, *args, **kwargs)

        for repeated in type_def.get('repeated', ()):
            least, most = 0, 0  # repetitions the alternatives can stand for, in total
            for alternative in repeated['alternatives']:
                types, members = alternative['types'], alternative['members']
                taken = [counts.get(m['index'], 0) for m in members]
                can_be_empty = not alternative['required'] and all(m['min'] == 0 for m in members)
                if types:
                    found = any(k in data and not self._check_value(data[k], types[k]) for k in types)
                    if not found and not any(taken):
                        most += 1 if can_be_empty else 0
                        continue
                    least += 1
                    most += 1  # one repetition: a fixed key cannot repeat
                    for key in alternative['required']:
                        if key not in data:
                            name = alternative['names'].get(key) or key
                            error_msg = f"Missing required field '{name}' (key {key}) in type '{type_name}'"
                            complain(error_msg, 'missing', at_parent=True,
                                     expected=f"member '{name}' (key {key})", actual='absent')
                    for member, count in zip(members, taken):
                        if count < member['min'] or (member['max'] is not None and count > member['max']):
                            complain(f"Type '{type_name}': an alternative of a repeated group choice takes "
                                     f"{_entries_wanted(member['min'], member['max'])} with key "
                                     f"'{format_node(computed[member['index']]['key'])}', found {count}")
                    continue
                # no fixed key: the alternative repeats as often as its entries need
                low = max([1 if any(taken) else 0] +
                          [-(-count // member['max']) for member, count in zip(members, taken)
                           if member['max']])
                high = min([unbounded] + [count // member['min'] for member, count in zip(members, taken)
                                          if member['min']])
                if not members:
                    high = unbounded  # nothing to take: any number of empty repetitions
                if high < low:
                    complain(f"Type '{type_name}': the entries of a repeated group choice cannot be "
                             f"divided into repetitions of its alternatives")
                least += low
                most += high
            wanted_low, wanted_high = repeated['min'], repeated['max']
            if (wanted_high is not None and least > wanted_high) or most < wanted_low:
                wanted = (f"at least {wanted_low}" if wanted_high is None else
                          f"exactly {wanted_low}" if wanted_low == wanted_high else
                          f"at most {wanted_high}" if wanted_low == 0 else f"{wanted_low} to {wanted_high}")
                found = least if least == most else f"{least} to {'many' if most == unbounded else most}"
                complain(f"Type '{type_name}' needs {wanted} repetitions of a group choice, found {found}")

    def _no_alternative(self, value: Any, alternatives, failures) -> _CheckError:
        """The error for a value that matches none of *alternatives*.

        *failures* are ``(alternative, error)`` pairs. The message names every
        alternative's reason; the finding expects the choice and has the value.
        """
        shown = ' / '.join(format_node(alt) for alt in alternatives)
        reasons = '; '.join(f"{format_node(alt)}: {error}" for alt, error in failures)
        return _CheckError(f"matches none of {shown} ({reasons})",
                           expected=' / '.join(self._alternative_text(alt) for alt in alternatives),
                           actual=_describe_value(value),
                           alternatives=tuple((self._alternative_text(alt), error if isinstance(error, _CheckError)
                                               else _CheckError(error)) for alt, error in failures))

    def _choice_from_values(self, expr) -> Optional[list]:
        """The values ``&( group )`` or ``&name`` chooses from, as type nodes.

        These are the types of the group's entries, keys dropped:
        ``&(a: 0, ? b: 1)`` gives the nodes ``0`` and ``1``. Entries that are
        groups (``&(base, c: 2)``) contribute their own entries. ``None`` when
        *expr* is not a choice from a group, or when the group cannot be
        expanded; :meth:`_check_value` reports the latter rather than
        accepting the value.
        """
        node = query.choice_from(expr)
        if node is None:
            return None
        group = node.group
        if group is None:
            resolved = self.cddl.resolved
            group = resolved.instantiate(node.name, node.args) if resolved is not None else None
            if group is None or not hasattr(group, 'choices'):
                return None
        values: list = []
        if not self._collect_group_values(group, values, set(), depth=0):
            return None
        return values

    def _collect_group_values(self, group, values: list, seen: set, depth: int) -> bool:
        """Add the entry types of *group* to *values*; False if one cannot be read.

        A group already being expanded (``g = (a: 1, ? g)``) adds nothing new
        and is skipped.
        """
        if depth > 64:
            return False
        for choice in group.choices:
            for entry in choice.entries:
                if isinstance(entry, Member):
                    values.extend(entry.value.alternatives)
                elif isinstance(entry, InlineGroup):
                    if not self._collect_group_values(entry.group, values, seen, depth + 1):
                        return False
                elif isinstance(entry, GroupRef):
                    key = (entry.name, entry.args)
                    if key in seen:
                        continue
                    seen.add(key)
                    resolved = self.cddl.resolved
                    inner = (resolved.instantiate(entry.name, entry.args)
                             if resolved is not None else None)
                    if inner is None or not hasattr(inner, 'choices') \
                            or not self._collect_group_values(inner, values, seen, depth + 1):
                        return False
                else:
                    return False
        return True

    @staticmethod
    def _is_socket(expr) -> bool:
        """True for a bare type socket name (``$name``)."""
        name = bare_name(as_type(expr))
        return name is not None and name.startswith('$') and not name.startswith('$$')

    def _is_empty_socket(self, expr) -> bool:
        """True for a type socket (``$name``) that has no alternatives."""
        if not self._is_socket(expr):
            return False
        name = bare_name(as_type(expr))
        return (name not in self.cddl.type_choices and name not in self.cddl.types
                and name not in self.cddl.type_aliases)
