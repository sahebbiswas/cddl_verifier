"""Loading the modules a schema imports (#121).

The CDDL modules draft (draft-ietf-cbor-cddl-modules) declares imports in
comments::

    ;# import rfc9393 as coswid
    ;# include extra-types

:func:`link` finds each named module, checks it as a schema of its own, and
adds its rules to the importing schema: as ``coswid.<rule>`` for an import
with ``as``, and under their own names for an include or an import without
``as``. References inside the module are renamed with the rules, so a module
keeps meaning what it meant on its own. Modules may import modules.

A module is looked up by name in a :class:`ModuleLoader`: first the modules
it was given explicitly, then ``<name>.cddl`` in each directory of its search
path. A module that is not found is not an error by default: its names stay
unchecked, as they were before modules could be loaded, and :class:`LinkResult`
says so. A :class:`ModuleLoader` made with ``strict=True`` fails closed instead
(#229): an import it cannot resolve is a :class:`CDDLSemanticError` at the
``;# import`` or ``;# include`` line.
"""

import os
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple, Union

from .._errors import SchemaError
from .ast import (ChoiceFrom, GroupRef, GroupRule, Name, Schema, Source, Span, TypeRule,
                  Unwrap)
from .errors import CDDLSemanticError
from .parser import parse_cddl
from .resolve import Imports, import_directives, resolve

#: A module given directly: CDDL text, or a path to a file holding it.
ModuleSource = Union[str, "os.PathLike[str]"]


class UnresolvedImportError(CDDLSemanticError):
    """An import a strict loader could not resolve (#229).

    A subclass so the module that imports another one can tell this error from a
    problem in the module itself, collect it and go on to the rest of its own
    directives, and report every unresolved import together.
    """


def _module_key(name: str) -> str:
    return name[:-5] if name.endswith('.cddl') else name


class ModuleLoader:
    """Finds the text of imported modules by name.

    Args:
        modules: ``module name -> CDDL text`` (``str``) or ``-> path``
            (``os.PathLike``), searched first. A trailing ``.cddl`` on a name
            is ignored.
        path: Directories searched, in order, for ``<name>.cddl``.
        strict: Treat an import that cannot be found as an error (#229), where
            by default its names are left unchecked and match any value.
    """

    def __init__(self, modules: Optional[Mapping[str, ModuleSource]] = None,
                 path: Iterable[Union[str, "os.PathLike[str]"]] = (),
                 strict: bool = False):
        self.modules = {_module_key(name): source for name, source in (modules or {}).items()}
        self.path = [Path(directory) for directory in path]
        self.strict = strict

    def find(self, name: str) -> Optional[Tuple[str, str]]:
        """``(text, source name)`` of module *name*, or ``None`` if it is not available.

        Raises:
            SchemaError: A module was found but cannot be read.
        """
        key = _module_key(name)
        if key in self.modules:
            source = self.modules[key]
            if isinstance(source, str):
                return source, f'<module {key}>'
            return self._read(Path(source), name)
        for directory in self.path:
            for candidate in (directory / f'{key}.cddl', directory / name):
                if candidate.is_file():
                    return self._read(candidate, name)
        return None

    @staticmethod
    def _read(path: Path, name: str) -> Tuple[str, str]:
        try:
            return path.read_text(encoding='utf-8'), str(path)
        except (OSError, UnicodeError) as e:
            raise SchemaError(f"cannot read CDDL module {name!r} ({path}): {e}") from e


@dataclass
class LinkResult:
    """A schema with its modules merged in.

    Attributes:
        schema:  The schema followed by the rules of the modules it imports.
        imports: Which imports were not found (and which prefixes were), for
            :func:`~cddl_verifier._cddl.resolve.resolve`.
        origins: ``rule name -> Source`` for each rule added from a module, to
            locate it.
        unloaded: ``(module, prefix or None)`` of each import that was not found.
        loaded:  Names of the modules that were merged in.
    """

    schema: Schema
    imports: Imports
    origins: Dict[str, Source] = field(default_factory=dict)
    unloaded: List[Tuple[str, Optional[str]]] = field(default_factory=list)
    loaded: List[str] = field(default_factory=list)


def link(schema: Schema, loader: Optional[ModuleLoader]) -> LinkResult:
    """Merge the modules *schema* imports into it.

    Raises:
        SchemaError: A module is malformed (the message names its file), imports
            itself through a cycle, or defines a name the schema or another
            module also defines.
    """
    return _Linker(loader or ModuleLoader()).link(schema, ())


class _Linker:
    def __init__(self, loader: ModuleLoader):
        self.loader = loader
        self.cache: Dict[str, Optional[LinkResult]] = {}

    def link(self, schema: Schema, stack: Tuple[str, ...]) -> LinkResult:
        text = schema.source.text if schema.source else ''
        rules = list(schema.rules)
        defined = {r.name for r in rules if r.assign == '=' and not r.name.startswith('$')}
        origins: Dict[str, Source] = {}
        unloaded: List[Tuple[str, Optional[str]]] = []
        loaded: List[str] = []
        loaded_prefixes: Dict[str, str] = {}
        prefixes: Set[str] = set()
        any_name = False
        done: Set[Tuple[str, Optional[str]]] = set()
        bound: Dict[str, str] = {}  # prefix -> the module it names
        sockets: Set[Tuple[str, str]] = set()  # (prefix, socket) the imports provide

        missing: List[UnresolvedImportError] = []  # strict: every import not found, in order

        for _kind, module, prefix, start, end in import_directives(text):
            if (module, prefix) in done:
                continue
            done.add((module, prefix))
            if prefix:
                if bound.setdefault(prefix, module) != module:
                    raise SchemaError(f"prefix '{prefix}' is used for both module "
                                      f"'{bound[prefix]}' and module '{module}'")
            try:
                result = self.module(module, stack)
            except UnresolvedImportError as nested:
                # the module has imports of its own that were not found: note them
                # where this import is and go on, so the rest of this schema's
                # directives are checked too
                missing.extend(nested.errors)
                continue
            if result is None:
                if self.loader.strict:
                    missing.append(self.not_found(module, prefix, Span(start, end), schema.source))
                    continue
                unloaded.append((module, prefix))
                if prefix:
                    prefixes.add(prefix)
                else:
                    any_name = True
                continue
            loaded.append(module)
            if prefix:
                loaded_prefixes[prefix] = module
            names = {r.name for r in result.schema.rules if not r.name.startswith('$')}
            if prefix:
                sockets |= {(prefix, r.name) for r in result.schema.rules
                            if r.name.startswith('$')}

            def mapping(n, p=prefix, names=names):
                return f'{p}.{n}' if p and n in names else n

            source = result.schema.source
            for rule in result.schema.rules:
                renamed = rename(rule, mapping)
                if renamed.assign == '=' and not renamed.name.startswith('$'):
                    if renamed.name in defined:
                        raise SchemaError(
                            f"module '{module}' defines '{renamed.name}', which the "
                            f"schema or another import also defines")
                    defined.add(renamed.name)
                rules.append(renamed)
                if source is not None:
                    origins[renamed.name] = source
            for name, origin in result.origins.items():
                origins[mapping(name)] = origin
            # what the module itself could not load stays unchecked here too
            prefixes |= result.imports.prefixes
            any_name = any_name or result.imports.any_name
            unloaded.extend(item for item in result.unloaded if item not in unloaded)

        if missing:
            # a module imported from two places is reported once, at its own directive
            unique: Dict[Tuple[Optional[str], int, int], UnresolvedImportError] = {}
            for error in missing:
                unique.setdefault((error.source.name, error.span.start, error.span.end), error)
            first = next(iter(unique.values()))
            first.errors = tuple(unique.values())
            raise first

        if sockets:
            # Sockets are shared by every module (the extensions of one name
            # merge), so a reference written with the import's prefix, as the
            # CoRIM schemas do (`coswid.$version-scheme`), is the socket itself.
            def socket_name(n, sockets=sockets):
                prefix, dot, rest = n.partition('.$')
                if dot and rest and (prefix, '$' + rest) in sockets:
                    return '$' + rest
                return n

            rules[:len(schema.rules)] = [rename(r, socket_name) for r in rules[:len(schema.rules)]]

        linked = replace(schema, rules=tuple(rules))
        return LinkResult(linked, Imports(frozenset(prefixes), any_name, loaded_prefixes),
                          origins, unloaded, loaded)

    def not_found(self, module: str, prefix: Optional[str], span: Span,
                  source: Optional[Source]) -> UnresolvedImportError:
        """The error for an import that cannot be resolved while the loader is strict (#229)."""
        how = f"imported as '{prefix}'" if prefix else "imported without a prefix"
        directories = ', '.join(str(directory) for directory in self.loader.path)
        searched = f"; searched {directories}" if directories else ""
        return UnresolvedImportError(
            f"module '{module}' ({how}) was not found{searched}; an import that cannot be "
            f"resolved is an error with strict_modules (--strict-modules)", span, source)

    def module(self, name: str, stack: Tuple[str, ...]) -> Optional[LinkResult]:
        """The linked and checked module *name*, or ``None`` if it is not available."""
        key = _module_key(name)
        if key in stack:
            raise SchemaError("CDDL modules import each other in a cycle: "
                              + ' -> '.join(stack[stack.index(key):] + (key,)))
        if key in self.cache:
            return self.cache[key]
        found = self.loader.find(name)
        if found is None:
            self.cache[key] = None
            return None
        text, source_name = found
        parsed = parse_cddl(text, source_name=source_name)
        result = self.link(parsed, stack + (key,))
        resolve(result.schema, result.imports)  # the module must make sense on its own
        self.cache[key] = result
        return result


# ------------------------------------------------------------------ renaming

def rename(node, mapping, scope: FrozenSet[str] = frozenset()):
    """A copy of *node* with every rule name passed through *mapping*.

    Rule names, and references to them as types, group entries, ``~x`` and
    ``&x``, are renamed. Generic parameters, which shadow rules inside their
    rule, and bareword map keys, which are text, are left alone.
    """
    if isinstance(node, (TypeRule, GroupRule)):
        inner = frozenset(node.params)
        body = rename(node.type if isinstance(node, TypeRule) else node.entry, mapping, inner)
        field_name = 'type' if isinstance(node, TypeRule) else 'entry'
        return replace(node, name=mapping(node.name), **{field_name: body})
    changes = {}
    if isinstance(node, (Name, GroupRef, Unwrap)) and node.name not in scope:
        changes['name'] = mapping(node.name)
    elif isinstance(node, ChoiceFrom) and node.name is not None and node.name not in scope:
        changes['name'] = mapping(node.name)
    for f in fields(node):
        if f.name in ('span', 'comment', 'name'):
            continue
        value = getattr(node, f.name)
        if isinstance(value, tuple):
            new = tuple(rename(item, mapping, scope) if _is_node(item) else item
                        for item in value)
        elif _is_node(value):
            new = rename(value, mapping, scope)
        else:
            continue
        changes[f.name] = new
    return replace(node, **changes) if changes else node


def _is_node(value) -> bool:
    return hasattr(value, '__dataclass_fields__') and not isinstance(value, type)


def unloaded_warnings(linked: LinkResult, external: Iterable[str]) -> List[str]:
    """One warning per import whose module was not found but whose names are relied on.

    *external* is ``ResolvedSchema.external``: the undefined names the schema
    uses and the resolver accepted because of an import.
    """
    external = sorted(external)
    prefixes = {prefix for _, prefix in linked.unloaded if prefix}
    warnings: List[str] = []
    for module, prefix in linked.unloaded:
        if prefix:
            names = [n for n in external if n.partition('.')[0] == prefix]
            if names:
                warnings.append(
                    f"module '{module}' (imported as '{prefix}') was not found, so "
                    f"{_count(names)} from it {'is' if len(names) == 1 else 'are'} not "
                    f"checked and match any value: "
                    f"{_sample(names)}")
        else:
            names = [n for n in external if n.partition('.')[0] not in prefixes]
            used = f" ({_count(names)}: {_sample(names)})" if names else ""
            warnings.append(
                f"module '{module}' was imported without a prefix and was not found, so "
                f"undefined names are not checked and match any value{used}")
    return warnings


def _count(names: List[str]) -> str:
    return f"{len(names)} name" + ("" if len(names) == 1 else "s")


def _sample(names: List[str], limit: int = 5) -> str:
    text = ', '.join(names[:limit])
    return text + (f", and {len(names) - limit} more" if len(names) > limit else "")
