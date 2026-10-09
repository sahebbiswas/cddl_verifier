"""Write an AST back as canonical CDDL text.

Single spaces, no comments. For every schema ``s``,
``parse_cddl(format_node(parse_cddl(s))) == parse_cddl(s)``: equality
ignores spans and comments (``docs/CDDL_AST_DESIGN.md`` §7).
"""

from .ast import (AnyItem, Array, BytesLit, ChoiceFrom, Control, FloatLit, Group,
                  GroupChoice, GroupRef, GroupRule, InlineGroup, IntLit, Major, Map,
                  Member, MemberKey, Name, Occurrence, Paren, Range, Schema, Tag,
                  TextLit, Type, TypeRule, Unwrap)


def format_node(node) -> str:
    """Return canonical CDDL for any AST node."""
    fn = _FORMATTERS.get(type(node))
    if fn is None:
        raise TypeError(f"not a CDDL AST node: {type(node).__name__}")
    return fn(node)


def _args(args) -> str:
    return '<' + ', '.join(format_node(a) for a in args) + '>' if args else ''


def _head(head) -> str:
    if head is None:
        return ''
    if isinstance(head, int):
        return f'.{head}'
    return f'.<{format_node(head)}>'


def _range(node: Range) -> str:
    op = '..' if node.inclusive else '...'
    low, high = format_node(node.low), format_node(node.high)
    if isinstance(node.low, (IntLit, FloatLit)):
        return f'{low}{op}{high}'
    # 'a..b' would lex as one identifier
    return f'{low} {op} {high}'


def _group(group: Group) -> str:
    return ' // '.join(_choice(c) for c in group.choices)


def _choice(choice: GroupChoice) -> str:
    return ', '.join(format_node(e) for e in choice.entries)


def _wrapped(open_: str, group: Group, close: str) -> str:
    inner = _group(group)
    return f'{open_} {inner} {close}' if inner.strip() else f'{open_}{close}'


def _occ(occurrence) -> str:
    return f'{occurrence.text} ' if occurrence is not None else ''


def _key(key: MemberKey) -> str:
    if key.kind == 'bareword':
        return f'{key.name}: '
    if key.kind == 'value':
        return f'{format_node(key.value)}: '
    return f"{format_node(key.type)} {'^ ' if key.cut else ''}=> "


def _rule_head(rule) -> str:
    params = '<' + ', '.join(rule.params) + '>' if rule.params else ''
    return f'{rule.name}{params} {rule.assign} '


_FORMATTERS = {
    Schema: lambda n: ''.join(format_node(r) + '\n' for r in n.rules),
    TypeRule: lambda n: _rule_head(n) + format_node(n.type),
    GroupRule: lambda n: _rule_head(n) + format_node(n.entry),
    Type: lambda n: ' / '.join(format_node(a) for a in n.alternatives),
    Range: _range,
    Control: lambda n: f'{format_node(n.target)} .{n.op} {format_node(n.arg)}',
    IntLit: lambda n: n.text,
    FloatLit: lambda n: n.text,
    TextLit: lambda n: n.text,
    BytesLit: lambda n: n.text,
    Name: lambda n: n.name + _args(n.args),
    Paren: lambda n: f'({format_node(n.type)})',
    Map: lambda n: _wrapped('{', n.group, '}'),
    Array: lambda n: _wrapped('[', n.group, ']'),
    Unwrap: lambda n: '~' + n.name + _args(n.args),
    ChoiceFrom: lambda n: (f'&({_group(n.group)})' if n.group is not None
                           else '&' + n.name + _args(n.args)),
    Tag: lambda n: f'#6{_head(n.tag)}({format_node(n.type)})',
    Major: lambda n: f'#{n.major}{_head(n.info)}',
    AnyItem: lambda n: '#',
    Group: _group,
    GroupChoice: _choice,
    Member: lambda n: _occ(n.occurrence) + (_key(n.key) if n.key else '') + format_node(n.value),
    GroupRef: lambda n: _occ(n.occurrence) + n.name + _args(n.args),
    InlineGroup: lambda n: _occ(n.occurrence) + f'({_group(n.group)})',
    MemberKey: lambda n: _key(n).rstrip(),
    Occurrence: lambda n: n.text,
}
