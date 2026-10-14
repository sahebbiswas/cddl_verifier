"""Values for CBOR items Python has no type for, and the record of how an item was written.

``undefined`` and the other simple values decode to :data:`UNDEFINED` and
:class:`CBORSimple` (#168). A tag decodes to a :class:`CBORTag` (#78).

The decoder keeps *how* an item was encoded only when asked (the validator
does, #171): the additional information of its initial byte, which says the
width of an integer, a length or a float, and whether a length was indefinite
(31). A value that was written the preferred way (shortest integer or length)
stays a plain ``int``, ``str``, ``bytes``, ``list`` or ``dict``, and
:func:`head_info` works out its width from the value. A value written another
way is an instance of a subclass of its Python type that carries ``info``:
it compares, hashes, pickles and prints as the plain value does. Floats have
no preferred width, so a float decoded with the record always carries it.
"""

from typing import Any, Dict, Optional

#: The ``info`` of a definite-length head is its additional information: the
#: argument itself below 24, or 24, 25, 26 and 27 for a 1, 2, 4 or 8 byte argument.
INDEFINITE = 31


class CBORUndefined:
    """The CBOR simple value ``undefined`` (``0xf7``). Use :data:`UNDEFINED`."""

    _instance: Optional['CBORUndefined'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return 'undefined'

    def __reduce__(self):
        return (CBORUndefined, ())

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


#: The decoded form of ``undefined``.
UNDEFINED = CBORUndefined()


class CBORSimple:
    """A simple value other than ``false``, ``true``, ``null`` and ``undefined``.

    *number* is 0 to 19 or 32 to 255 (``simple(16)``). 20 to 23 are the four
    named values and 24 to 31 cannot be written as simple values (RFC 8949
    §3.3).
    """

    __slots__ = ('number',)

    def __init__(self, number: int):
        if isinstance(number, bool) or not isinstance(number, int) \
                or not (0 <= number <= 19 or 32 <= number <= 255):
            raise ValueError(f"not a simple value number: {number!r} "
                             f"(0 to 19 or 32 to 255; 20 to 23 are false, true, null and "
                             f"undefined)")
        object.__setattr__(self, 'number', number)

    def __setattr__(self, name, value):
        raise AttributeError("CBORSimple is immutable")

    def __eq__(self, other):
        return isinstance(other, CBORSimple) and other.number == self.number

    def __hash__(self):
        return hash(('simple', self.number))

    def __repr__(self) -> str:
        return f'simple({self.number})'

    def __reduce__(self):
        return (CBORSimple, (self.number,))


class CBORTag:
    """A tagged item (RFC 8949 section 3.4): tag number *tag* and the tagged *value*.

    The decoder gives every tag as a ``CBORTag``, and the encoder writes one as
    a tag. It is immutable and equal only to another ``CBORTag`` with the same
    tag number and an equal value, so a plain two-element tuple, which Python
    code also uses for an array, is never a tag.

    Unpacking works: ``tag, value = CBORTag(1, 100)``.

    Example:
        >>> CBORTag(1, 1700000000)
        CBORTag(1, 1700000000)
        >>> encode(CBORTag(32, "https://example.com")).hex()[:4]
        'd820'
    """

    __slots__ = ('tag', 'value')

    def __init__(self, tag: int, value: Any):
        """
        Raises:
            TypeError: *tag* is not an ``int`` (a ``bool`` is not).
            ValueError: *tag* is outside 0 to 2**64 - 1.
        """
        if isinstance(tag, bool) or not isinstance(tag, int):
            raise TypeError(f"a tag number is an int, not {type(tag).__name__}")
        if not 0 <= tag < 2 ** 64:
            raise ValueError(f"a tag number is 0 to 2**64 - 1, not {tag}")
        object.__setattr__(self, 'tag', tag)
        object.__setattr__(self, 'value', value)

    def __setattr__(self, name, value):
        raise AttributeError("CBORTag is immutable")

    def __delattr__(self, name):
        raise AttributeError("CBORTag is immutable")

    def __eq__(self, other):
        if isinstance(other, CBORTag):
            return self.tag == other.tag and self.value == other.value
        return NotImplemented

    def __hash__(self):
        return hash(('tag', self.tag, self.value))

    def __iter__(self):
        yield self.tag
        yield self.value

    def __repr__(self) -> str:
        return f'CBORTag({self.tag!r}, {self.value!r})'

    def __reduce__(self):
        return (CBORTag, (self.tag, self.value))


def tag_tuple_error(obj: Any) -> Optional[TypeError]:
    """The error for a tuple shaped like the old tag form, or ``None``.

    Before #78 a ``(tag, value)`` tuple was a tag, so ``(1, 2)`` could not be
    an array of two integers. It is now neither: say which was meant.
    """
    if (isinstance(obj, tuple) and len(obj) == 2 and isinstance(obj[0], int)
            and not isinstance(obj[0], bool) and obj[0] >= 0):
        return TypeError(
            f"{obj!r} is ambiguous: write CBORTag({obj[0]}, ...) for a tag, or use a list "
            f"for an array (a (tag, value) tuple stopped being a tag in 0.24.0)")
    return None


def shortest_info(argument: int) -> int:
    """The additional information of the shortest head for *argument* (below 2**64)."""
    if argument < 24:
        return argument
    if argument < 2 ** 8:
        return 24
    if argument < 2 ** 16:
        return 25
    if argument < 2 ** 32:
        return 26
    return 27


# ---------------------------------------------------------------- encoded values

def _encoded_class(base: type) -> type:
    def __reduce__(self):
        return (base, (base(self),))

    cls = type(base.__name__, (base,), {'__reduce__': __reduce__, '__module__': __name__,
                                        '__doc__': f"A {base.__name__} that remembers how it "
                                                   f"was encoded (``info``)."})
    return cls


_ENCODED: Dict[type, type] = {base: _encoded_class(base)
                              for base in (int, float, str, bytes, list, dict)}
_ENCODED_TYPES = tuple(_ENCODED.values())


def with_info(value: Any, info: int) -> Any:
    """*value* carrying the additional information *info* of its encoding."""
    wrapped = _ENCODED[type(value)](value)
    wrapped.info = info
    return wrapped


def head_info(value: Any) -> Optional[int]:
    """The additional information of the head *value* was (or would be) written with.

    ``None`` when it is not known or has no meaning: a float that was not
    decoded with the record, a bignum, a simple value, a tag.
    """
    if isinstance(value, _ENCODED_TYPES):
        return value.info
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        argument = value if value >= 0 else -1 - value
        return shortest_info(argument) if argument < 2 ** 64 else None
    if isinstance(value, (bytes, list, dict)):
        return shortest_info(len(value))
    if isinstance(value, str):
        return shortest_info(len(value.encode('utf-8')))
    return None
