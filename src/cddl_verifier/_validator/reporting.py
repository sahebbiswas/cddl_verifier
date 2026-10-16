"""Breadcrumbs, locations, offsets and the error and diagnostic reports.

Moved verbatim out of ``_analyzer`` (#83); ``CBORAnalyzer`` composes it.
"""

import contextlib
from typing import Any, List, Optional, Tuple

from .._cbor import CBOR, CBORDecodeError
from .._diagnostics import (
    _Finding,
    classify_message,
    Diagnostic,
    diagnostic_of,
    expected_and_found,
    MAX_ALTERNATIVES,
    MAX_HEX_BYTES,
)
from .._log import Colors, logger
from .support import _CheckError, _CONTROL_CHARS, _EmbeddedFrame, _NO_STEP, MAJOR_TYPE_ARRAY, MAJOR_TYPE_MAP


class ReportingMixin:
    """Breadcrumbs, locations, offsets and the error and diagnostic reports."""

    def _push_breadcrumb(self, component: str, step: Any = _NO_STEP):
        """Add a component to the CDDL breadcrumb.

        *step* is the map key or array index of the data item the component
        stands for; a rule name has none.
        """
        self.breadcrumb.append(component)
        self._data_path.append(step)
    
    def _pop_breadcrumb(self):
        """Remove the last component from the breadcrumb."""
        if self.breadcrumb:
            self.breadcrumb.pop()
            if self._data_path:
                self._data_path.pop()

    def _current_path(self) -> Tuple[Any, ...]:
        """Map keys and array indexes from the root to the item being checked."""
        return tuple(step for step in self._data_path if step is not _NO_STEP)

    def _item_span(self, path: Tuple[Any, ...], key: Any = _NO_STEP,
                   fallback: bool = True) -> Optional[Tuple[int, int]]:
        """Byte span of the item at *path* (or of its key *key*) in the CBOR input.

        Inside embedded CBOR an item the index has no position for is placed
        at its byte string, unless *fallback* is false: then it has no span.
        """
        if self._embedded_frames:
            return self._embedded_item_span(path, key, fallback)
        if not self.cbor_bytes:
            return None
        if self._locations is None and not self._locations_tried:
            self._locations_tried = True
            try:
                self._locations = CBOR.loads_with_locations(self.cbor_bytes)[1]
            except (CBORDecodeError, TypeError):
                self._locations = None
        if self._locations is None:
            return None
        if key is not _NO_STEP:
            return self._locations.key_span(path, key)
        return self._locations.span(path)

    def _embedded_item_span(self, path: Tuple[Any, ...], key: Any,
                            fallback: bool = True) -> Optional[Tuple[int, int]]:
        """Span, in the input, of an item inside embedded CBOR.

        The embedded bytes are a slice of the input, so the item's position
        inside them plus where they start is its offset in the input. An item
        the index has no position for (or bytes that are not one slice, such
        as a chunked byte string) is reported at the byte string.
        """
        frame = self._embedded_frames[-1]
        if frame.locations is not None:
            below = path[frame.depth:]
            span = (frame.locations.key_span(below, key) if key is not _NO_STEP
                    else frame.locations.span(below))
            if span is not None:
                return frame.base + span[0], frame.base + span[1]
        return frame.span if fallback else None

    @contextlib.contextmanager
    def _inside_embedded_cbor(self, payload: bytes, sequence: bool = False):
        """Report offsets inside the byte string *payload* while checking the CBOR in it.

        *sequence* is for ``.cborseq``: the items are addressed as an array.
        """
        path = self._current_path()
        span = self._item_span(path)  # where to point when the bytes can't be located
        own = self._item_span(path, fallback=False)  # the byte string's own span, if indexed
        base = locations = None
        if own is not None and self.cbor_bytes:
            start = own[1] - len(payload)  # the content is the end of the byte string item
            if start >= own[0] and self.cbor_bytes[start:own[1]] == payload:
                try:
                    locations = (CBOR.loads_sequence_with_locations(payload)[1] if sequence
                                 else CBOR.loads_with_locations(payload)[1])
                    base = start
                except (CBORDecodeError, TypeError):
                    locations = None
        self._embedded_frames.append(
            _EmbeddedFrame(len(path), base or 0, locations, span))
        try:
            yield
        finally:
            self._embedded_frames.pop()

    def _report(self, message: str, category: Optional[str] = None, *,
                expected: Optional[str] = None, actual: Optional[str] = None,
                rule: Optional[str] = None, at_parent: bool = False,
                extra_path: Tuple[Any, ...] = (), key: Any = _NO_STEP,
                finding: Optional[_CheckError] = None) -> None:
        """Record a validation error as a message with its structured diagnostic.

        The message is appended to :attr:`validation_errors` unchanged; the
        diagnostic holds the data path, the schema path and rule, the byte
        offset and bytes of the item, and what was expected and found.

        Args:
            message:   The error text.
            category:  A category from :data:`_diagnostics.CATEGORIES`;
                       guessed from the message when omitted.
            expected:  What the schema asks for; read from an "expected X,
                       got Y" message when omitted.
            actual:    What the data has.
            rule:      The rule being checked; defaults to the innermost one.
            at_parent: Point at the container of the current item (a member
                       that is missing has no item of its own).
            extra_path: Steps below the current item.
            key:       Point at the member with this key of the map at the current
                       item: the path ends at it and the offset is that of its key.
            finding:   The error from :meth:`_check_value` that *message* words:
                       the diagnostic takes its path below the current item,
                       its category, expected and actual from it, unless given.
        """
        path = self._current_path()
        if at_parent and path:
            path = path[:-1]
        path = path + tuple(extra_path)
        diagnostic = self._diagnostic(message, path, category, expected, actual, rule, key, finding)
        self.validation_errors.append(_Finding(message, diagnostic))

    def _diagnostic(self, message: str, path: Tuple[Any, ...], category: Optional[str],
                    expected: Optional[str], actual: Optional[str], rule: Optional[str],
                    key: Any, finding: Optional[_CheckError],
                    schema_path: Optional[Tuple[str, ...]] = None) -> Diagnostic:
        """The diagnostic for *message* at *path*, below the schema path (the current one by default)."""
        schema_path = tuple(self.breadcrumb) if schema_path is None else schema_path
        alternatives = ()
        if finding is not None:
            path = path + finding.path
            schema_path = schema_path + finding.schema_path
            # the alternatives were checked on the same value: they continue from here
            alternatives = tuple(
                item if isinstance(item, Diagnostic)  # already complete (from a nested run)
                else self._diagnostic(f"{item[0]}: {item[1]}", path, None, None, None, None,
                                      _NO_STEP, item[1], schema_path)
                for item in finding.alternatives[:MAX_ALTERNATIVES])
            category = category or finding.category
            rule = rule or finding.rule
            if expected is None and actual is None:
                expected, actual = finding.expected, finding.actual
        if category is None:
            category = classify_message(message)
        if expected is None and actual is None:
            expected, actual = expected_and_found(message)
        if finding is not None and finding.located:
            span = finding.span  # found when it was checked, inside embedded CBOR or not
        else:
            span = self._item_span(path, key)
        if key is not _NO_STEP:
            path = path + (key,)  # the member's own path; the offset is that of its key
        offset = end = hex_text = None
        truncated = False
        if span is not None:
            offset, end = span
            if self.cbor_bytes:
                excerpt = self.cbor_bytes[offset:min(end, offset + MAX_HEX_BYTES)]
                hex_text = excerpt.hex(' ')
                truncated = end - offset > MAX_HEX_BYTES
        rule_name = rule if rule is not None else (self._rules[-1] if self._rules else None)
        return Diagnostic(
            message=message, code="validation", severity="error", offset=offset,
            category=category, path=path, schema_path=schema_path,
            rule=rule_name,
            schema_location=self.cddl.rule_location(rule_name) if rule_name else None,
            expected=expected, actual=actual, end_offset=end, hex=hex_text,
            hex_truncated=truncated, alternatives=alternatives)
    
    def _get_breadcrumb(self) -> str:
        """Get the current CDDL path as a string."""
        if not self.breadcrumb:
            return "/"
        return "/" + "/".join(self.breadcrumb)
    
    def _get_cbor_context(self, value: Any) -> str:
        """Get CBOR byte context for a value if available."""
        if not self.cbor_bytes:
            return ""
        
        # Try to find this value's offset in our map
        value_id = id(value)
        if value_id in self.offset_map:
            offset = self.offset_map[value_id]
            if offset < len(self.cbor_bytes):
                # Show a few bytes around this offset
                start = max(0, offset)
                end = min(len(self.cbor_bytes), offset + 4)
                hex_bytes = self.cbor_bytes[start:end].hex(' ')
                return f" {Colors.CBOR}[@{offset:04x}:{hex_bytes}]{Colors.RESET}"
        
        return ""
    
    def _build_offset_map(self, cbor_bytes: bytes, data: Any, offset: int = 0):
        """Build a map of Python object IDs to CBOR byte offsets.
        
        This is a simplified approach - walks the data structure and estimates offsets.
        """
        try:
            if offset >= len(cbor_bytes):
                return offset
            
            initial_byte = cbor_bytes[offset]
            major_type = initial_byte >> 5
            additional_info = initial_byte & 0x1F
            
            # Store this value's offset
            self.offset_map[id(data)] = offset
            
            current_offset = offset + 1
            
            # For maps and arrays, recursively process children
            if major_type == MAJOR_TYPE_MAP and isinstance(data, dict):
                # Decode length (not currently used, but needed to advance offset)
                _length = additional_info
                if additional_info == 24:
                    _length = cbor_bytes[current_offset] if current_offset < len(cbor_bytes) else 0
                    current_offset += 1
                elif additional_info == 25:
                    if current_offset + 1 < len(cbor_bytes):
                        _length = int.from_bytes(cbor_bytes[current_offset:current_offset+2], 'big')
                    current_offset += 2
                
                # Process each key-value pair
                for key, value in data.items():
                    # Process key
                    current_offset = self._skip_cbor_item(cbor_bytes, current_offset)
                    # Process value
                    if current_offset < len(cbor_bytes):
                        current_offset = self._build_offset_map(cbor_bytes, value, current_offset)
                
                return current_offset
            
            elif major_type == MAJOR_TYPE_ARRAY and isinstance(data, (list, tuple)):
                # Decode length (not currently used, but needed to advance offset)
                _length = additional_info
                if additional_info == 24:
                    _length = cbor_bytes[current_offset] if current_offset < len(cbor_bytes) else 0
                    current_offset += 1
                
                for item in data:
                    if current_offset < len(cbor_bytes):
                        current_offset = self._build_offset_map(cbor_bytes, item, current_offset)
                
                return current_offset
            
            else:
                # For primitives, skip to next item
                return self._skip_cbor_item(cbor_bytes, offset)
        
        except Exception as e:
            logger.debug(f"Error building offset map: {e}")
            return offset
    
    def _skip_cbor_item(self, cbor_bytes: bytes, offset: int) -> int:
        """Skip a CBOR item and return the offset of the next item."""
        if offset >= len(cbor_bytes):
            return offset
        
        initial_byte = cbor_bytes[offset]
        major_type = initial_byte >> 5
        additional_info = initial_byte & 0x1F
        
        offset += 1
        
        # Decode length/value based on additional info
        if additional_info < 24:
            value_length = 0
        elif additional_info == 24:
            value_length = 1
        elif additional_info == 25:
            value_length = 2
        elif additional_info == 26:
            value_length = 4
        elif additional_info == 27:
            value_length = 8
        else:
            value_length = 0
        
        offset += value_length
        
        # For strings and byte strings, skip the actual data
        if major_type in [2, 3]:  # byte string or text string
            if additional_info < 24:
                data_length = additional_info
            elif additional_info == 24 and offset - 1 < len(cbor_bytes):
                data_length = cbor_bytes[offset - 1]
            elif additional_info == 25 and offset - 2 < len(cbor_bytes):
                data_length = int.from_bytes(cbor_bytes[offset-2:offset], 'big')
            else:
                data_length = 0
            
            offset += data_length
        
        return offset

    def _format_value_for_log(self, value, max_len=50):
        """Format a value for logging, with truncation."""
        if isinstance(value, bytes):
            hex_str = value[:4].hex(' ')
            if len(value) > 4:
                return f"h'{hex_str}...' ({len(value)} bytes)"
            return f"h'{hex_str}'"
        elif isinstance(value, str):
            # Control characters are escaped so a hostile string cannot move the
            # cursor or rewrite the terminal when a diagnostic is printed.
            text = value[:max_len].translate(_CONTROL_CHARS)
            return f'"{text}..."' if len(value) > max_len else f'"{text}"'
        elif isinstance(value, (list, tuple)):
            return f"[{len(value)} items]"
        elif isinstance(value, dict):
            return f"{{{len(value)} fields}}"
        else:
            return str(value)
    def get_errors(self) -> List[str]:
        """Return the list of validation error messages from the last :meth:`validate` call.

        Returns an empty list when the last call succeeded.  The list is
        reset at the start of every :meth:`validate` call.
        """
        return self.validation_errors

    def get_diagnostics(self) -> Tuple[Diagnostic, ...]:
        """Return the structured diagnostics of the last :meth:`validate` call.

        One per message of :meth:`get_errors`, in the same order. Empty when
        the last call succeeded.
        """
        return tuple(diagnostic_of(error) for error in self.validation_errors)
