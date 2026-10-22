"""The ``cddl-verify`` command: argument parsing, loading, validation and output.

Split from ``_analyzer`` (#83). Use ``cddl_verifier.cli.main`` or the console script.
"""

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from ._analyzer import CBORAnalyzer
from ._cbor import CBOR, CBORDecodeError
from ._cddl.modules import ModuleLoader
from ._cddl.printer import format_node
from ._decode import SimpleCBORDecoder
from ._edn import EDNGenerator
from ._diagnostics import (Diagnostic, decode_diagnostic, render_json, render_text,
                           schema_diagnostic)
from ._schema import CDDLParser
from ._log import _configure_cli_logging, _reset_cli_logging, logger
from ._version import __version__


def load_cddl(filepath: Path, loader: Optional[ModuleLoader] = None,
              comment_names: bool = False) -> CDDLParser:
    """Load a CDDL schema file from *filepath* and return a :class:`CDDLParser`.

    Args:
        filepath: Path to the ``.cddl`` file.
        loader: Finds the modules the schema imports. By default modules are
            looked up next to the schema file.
        comment_names: Name members after their trailing comments (see
            :class:`CDDLParser`).

    Returns:
        A fully parsed :class:`CDDLParser` instance ready for validation or
        EDN generation.

    Exits:
        Calls ``sys.exit(1)`` on any I/O or parse error (CLI helper).
    """
    try:
        content = filepath.read_text(encoding='utf-8')
        if loader is None:
            loader = ModuleLoader(path=[filepath.parent])
        return CDDLParser(content, source_name=str(filepath), loader=loader,
                          comment_names=comment_names)
    except Exception as e:
        print(f"Error loading CDDL file: {e}")
        sys.exit(1)


def load_cbor(filepath: Path) -> Any:
    """Load and decode a CBOR binary file from *filepath*.

    Tries ``cbor2.load`` first; falls back to the bundled
    :class:`SimpleCBORDecoder` if ``cbor2`` is not installed.

    Args:
        filepath: Path to the ``.cbor`` binary file.

    Returns:
        The decoded Python object (``dict``, ``list``, scalar, etc.).

    Exits:
        Calls ``sys.exit(1)`` on any I/O or decode error (CLI helper).
    """
    try:
        # Try to use cbor2 if available
        try:
            import cbor2
            with open(filepath, 'rb') as f:
                return cbor2.load(f)
        except ImportError:
            # Fall back to simple decoder
            with open(filepath, 'rb') as f:
                data = f.read()
            decoder = SimpleCBORDecoder(data)
            return decoder.decode()
    except Exception as e:
        print(f"Error loading CBOR file: {e}")
        sys.exit(1)


def main(argv=None):
    """Entry point of the ``cddl-verify`` command (see ``--help``)."""
    try:
        _run_cli(argv)
    finally:
        _reset_cli_logging()


def _exit_json(diagnostics, *, valid, root_type=None, status=1, features_used=()):
    """Print the diagnostics document, the only thing on stdout, and exit."""
    extra = {"features_used": list(features_used)} if features_used else {}
    sys.stdout.write(render_json(diagnostics, valid=valid, root_type=root_type, **extra) + "\n")
    sys.stdout.flush()
    sys.exit(status)


def _schema_problems(cddl_file, modules, module_path, error, strict_modules=False, bundled_modules=True):
    """Every problem in the schema that could not be loaded, as diagnostics (#145)."""
    from ._api import schema_diagnostics  # _api imports this module
    try:
        found = schema_diagnostics(cddl_file, modules=modules, module_path=module_path,
                                   strict_modules=strict_modules, bundled_modules=bundled_modules)
    except Exception:  # noqa: BLE001 - fall back to the error that stopped the load
        found = ()
    return found or (schema_diagnostic(error),)


def _run_cli(argv=None):
    parser = argparse.ArgumentParser(
        prog='cddl-verify',
        description='Analyze CBOR against CDDL and generate annotated EDN',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Decode CBOR and display annotated EDN (stdout)
  %(prog)s schema.cddl data.cbor

  # Validate CBOR against a named type and display annotated EDN
  %(prog)s schema.cddl data.cbor --type corim-map

  # Write annotated EDN to a file
  %(prog)s schema.cddl data.cbor --type corim-map --output data.edn

  # Suppress field-name annotations
  %(prog)s schema.cddl data.cbor --no-annotate

  # Use human-readable key names instead of integer indices
  %(prog)s schema.cddl data.cbor --edn-format keyname

  # Reject CBOR that is not deterministically encoded (signed payloads)
  %(prog)s schema.cddl data.cbor --type corim-map --require-deterministic

  # Machine-readable diagnostics: stdout is one JSON document, nothing else
  %(prog)s schema.cddl data.cbor --type corim-map --diagnostics json

  # ...and the EDN to a file when the data is valid
  %(prog)s schema.cddl data.cbor --type corim-map --diagnostics json -o data.edn
        """
    )
    
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument('cddl_file', type=Path, help='Path to CDDL schema file')
    parser.add_argument('cbor_file', type=Path, help='Path to CBOR data file')
    parser.add_argument('-o', '--output', type=Path, help='Output EDN file (default: stdout)')
    parser.add_argument('-t', '--type', help='Root type name from CDDL for validation')
    # Annotations are on by default.  Use --no-annotate to suppress field-name comments.
    # The old -a/--annotate flag was removed: `action='store_true', default=True` meant
    # passing -a never changed anything — the flag was always True either way.
    parser.set_defaults(annotate=True)
    parser.add_argument('--no-annotate', action='store_false', dest='annotate',
                        help='Suppress field-name comments in EDN output')
    parser.add_argument('--comment-names', action='store_true',
                        help='Name a member after its trailing comment (0: tstr ; name) in messages '
                             'and EDN annotations. Off by default: comments carry no meaning in CDDL '
                             '(RFC 8610), so a member is named by its key')
    parser.add_argument('--edn-format', choices=['keyindex', 'keyname', 'both'], default='keyindex',
                        help='EDN key format: keyindex (0: val / name /), keyname ("name": val), both (0 / name /: val)')
    parser.add_argument('-M', '--module-path', action='append', default=[], metavar='DIR',
                        type=Path,
                        help='Directory searched for imported CDDL modules (<name>.cddl); '
                             'repeatable. The schema file\'s own directory is searched last')
    parser.add_argument('--module', action='append', default=[], metavar='NAME=FILE',
                        help='CDDL file for the imported module NAME (;# import NAME as P); '
                             'repeatable')
    parser.add_argument('--no-bundled-modules', action='store_true',
                        help='Do not fall back to the CDDL modules the package ships (rfc9393, '
                             'rfc9164) for an import that --module and --module-path do not supply')
    parser.add_argument('--strict-modules', action='store_true',
                        help='Fail when an imported CDDL module (;# import / ;# include) cannot '
                             'be found, instead of leaving its names unchecked with a warning')
    parser.add_argument('--unique-choices', action='store_true',
                        help='Fail when data matches more than one map, array or tag '
                             'alternative of a choice')
    parser.add_argument('--feature', action='append', default=None, metavar='NAME',
                        help='Enable the RFC 9165 feature NAME for ".feature" controls; repeatable. '
                             'With no --feature and no --no-features every feature is enabled; '
                             'with one or more --feature options only the named features are enabled.')
    parser.add_argument('--no-features', action='store_true',
                        help='Enable no RFC 9165 feature: "T .feature NAME" matches nothing')
    parser.add_argument('--diagnostics', choices=['text', 'json'], default='text',
                        help='How problems are reported: text (messages on stderr, the default) '
                             'or json (one JSON document on stdout, progress and logs on stderr; '
                             'EDN is written only with -o)')
    parser.add_argument('--require-deterministic', action='store_true',
                        help='Fail when the CBOR is not deterministically encoded '
                             '(RFC 8949 section 4.2.1): shortest heads and floats, '
                             'definite lengths, map keys in bytewise order')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable verbose logging of validation and type resolution')
    parser.add_argument('--show-types', action='store_true', 
                        help='Show parsed CDDL types and exit')
    
    args = parser.parse_args(argv)
    if args.feature and args.no_features:
        parser.error("--feature and --no-features cannot be used together")
    # no option: every feature; --feature NAME: only those; --no-features: none
    features = frozenset(args.feature or ()) if (args.feature or args.no_features) else None

    # Set logging level
    _configure_cli_logging(args.verbose)
    if args.verbose:
        logger.debug("Verbose logging enabled")
    
    json_mode = args.diagnostics == 'json'
    if json_mode and args.show_types:
        parser.error("--show-types cannot be combined with --diagnostics json")

    # Check if files exist
    if not args.cddl_file.exists():
        if json_mode:
            _exit_json([Diagnostic(f"CDDL file not found: {args.cddl_file}", code="schema",
                                   category="schema")], valid=False)
        print(f"Error: CDDL file not found: {args.cddl_file}")
        sys.exit(1)
    
    if not args.cbor_file.exists():
        if json_mode:
            _exit_json([Diagnostic(f"CBOR file not found: {args.cbor_file}", code="decode",
                                   category="decode")], valid=False)
        print(f"Error: CBOR file not found: {args.cbor_file}")
        sys.exit(1)
    
    # Load CDDL schema
    print(f"Loading CDDL schema: {args.cddl_file}", file=sys.stderr)
    modules = {}
    for item in args.module:
        name, sep, file = item.partition('=')
        if not sep or not name or not file:
            parser.error(f"--module expects NAME=FILE, not {item!r}")
        modules[name] = Path(file)
    loader = ModuleLoader(modules, [*args.module_path, args.cddl_file.parent],
                          strict=args.strict_modules, bundled=not args.no_bundled_modules)
    try:
        cddl = CDDLParser(args.cddl_file.read_text(encoding='utf-8'),
                          source_name=str(args.cddl_file), loader=loader,
                          comment_names=args.comment_names)
    except Exception as e:  # noqa: BLE001 - any read or parse error is a schema problem
        problems = _schema_problems(args.cddl_file, modules, args.module_path, e,
                                    args.strict_modules, not args.no_bundled_modules)
        if json_mode:
            _exit_json(problems, valid=False)
        print("Error loading CDDL file:", file=sys.stderr)
        for line in render_text(problems).splitlines():
            print(f"  - {line}", file=sys.stderr)
        sys.exit(1)
    for message in cddl.warnings:
        print(f"Warning: {message}", file=sys.stderr)
    
    # Show types if requested
    if args.show_types:
        print("\nParsed CDDL Types:", file=sys.stderr)
        print("=" * 50, file=sys.stderr)
        for type_name, type_def in cddl.types.items():
            print(f"\n{type_name} ({type_def['type']}):", file=sys.stderr)
            for key, field_info in type_def['fields'].items():
                opt = " (optional)" if field_info.get('optional') else ""
                registered = " [IANA registered]" if field_info.get('registered') else ""
                print(f"  {key}: {field_info['name']} -> {format_node(field_info['type'])}{opt}{registered}", 
                      file=sys.stderr)
        
        # Show groups
        if cddl.groups:
            print("\n" + "=" * 50, file=sys.stderr)
            print("CDDL Groups:", file=sys.stderr)
            print("=" * 50, file=sys.stderr)
            for group_name, group_fields in cddl.groups.items():
                print(f"\n{group_name}:", file=sys.stderr)
                for field in group_fields:
                    print(f"  {field}", file=sys.stderr)
        
        # Show type choices
        if cddl.type_choices:
            print("\n" + "=" * 50, file=sys.stderr)
            print("Type Choices:", file=sys.stderr)
            print("=" * 50, file=sys.stderr)
            for choice_name, alternatives in cddl.type_choices.items():
                print(f"\n{choice_name}:", file=sys.stderr)
                for alt in alternatives:
                    print(f"  /= {format_node(alt)}", file=sys.stderr)
        
        # Show socket extensions
        if cddl.socket_extensions:
            print("\n" + "=" * 50, file=sys.stderr)
            print("Socket Extensions:", file=sys.stderr)
            print("=" * 50, file=sys.stderr)
            for socket_name, extensions in cddl.socket_extensions.items():
                print(f"\n{socket_name}:", file=sys.stderr)
                for ext in extensions:
                    print(f"  //= {ext}", file=sys.stderr)
        
        # Show global registered parameters
        if cddl.registered_params:
            print("\n" + "=" * 50, file=sys.stderr)
            print("Global IANA Registered Parameters:", file=sys.stderr)
            print("=" * 50, file=sys.stderr)
            for keyindex, keyname in sorted(cddl.registered_params.items()):
                print(f"  {keyindex} -> {keyname}", file=sys.stderr)
        
        sys.exit(0)
    
    # Load CBOR data — single read, then decode in-memory (avoids opening the file twice)
    print(f"Loading CBOR data: {args.cbor_file}", file=sys.stderr)
    try:
        cbor_bytes = args.cbor_file.read_bytes()
    except OSError as e:  # a directory, or a file that cannot be read
        if json_mode:
            _exit_json([Diagnostic(f"Cannot read CBOR file {args.cbor_file}: {e}",
                                   code="decode", category="decode")], valid=False)
        print(f"Error: cannot read CBOR file {args.cbor_file}: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        cbor_data = CBOR.loads(cbor_bytes, keep_encoding=True,
                               require_deterministic=args.require_deterministic)
    except Exception as _e:  # noqa: BLE001 — loader raises CBORDecodeError (empty/truncated/trailing/malformed) or TypeError
        if json_mode:
            _exit_json([decode_diagnostic(_e, cbor_bytes) if isinstance(_e, CBORDecodeError)
                        else Diagnostic(str(_e), code="decode", category="decode")], valid=False)
        print(f"Error decoding CBOR: {_e}", file=sys.stderr)
        sys.exit(1)
    
    # Resolve the root type: explicit (--type) or inferred from the first CDDL entry
    root_type: Optional[str] = args.type
    type_inferred = False
    if root_type is None and cddl.first_definition:
        root_type = cddl.first_definition
        type_inferred = True
        print(f"No --type specified; using first CDDL entry: '{root_type}'", file=sys.stderr)

    # Validate against the root type.
    # - Explicit --type: hard failure on validation errors (existing behaviour).
    # - Inferred type:   log errors as warnings, then continue to show EDN output.
    checked_type, valid, diagnostics, features_used = root_type, None, (), ()
    if root_type:
        print(f"Validating CBOR against CDDL (type: {root_type})...", file=sys.stderr)
        cddl.features = features  # EDN output picks choices by the same rule
        analyzer = CBORAnalyzer(cddl, unique_choices=args.unique_choices, features=features)

        if analyzer.validate(cbor_data, root_type, cbor_bytes):
            valid = True
            features_used = analyzer.features_used
            print("[OK] Validation successful", file=sys.stderr)
            if features_used:
                print(f"Features used: {', '.join(features_used)}", file=sys.stderr)
        else:
            valid = False
            errors = analyzer.get_errors()
            diagnostics = analyzer.get_diagnostics()
            if type_inferred:
                # Non-fatal: warn and fall back to unannotated EDN output
                print("[WARNING] Type resolution/validation errors for inferred type "
                      f"'{root_type}' (proceeding to EDN output):", file=sys.stderr)
                for error in errors:
                    logger.warning(error)
                diagnostics = tuple(replace(d, severity="warning") for d in diagnostics)
                root_type = None  # Produce plain EDN without type annotations
            else:
                if json_mode:
                    _exit_json(diagnostics, valid=False, root_type=checked_type)
                print("[FAIL] Validation failed:", file=sys.stderr)
                for error in errors:
                    print(f"  - {error}", file=sys.stderr)
                sys.exit(1)

    if json_mode and not args.output:
        _exit_json(diagnostics, valid=valid, root_type=checked_type, status=0,
                   features_used=features_used)

    # Generate EDN
    print("Generating EDN...", file=sys.stderr)
    generator = EDNGenerator(cddl, edn_format=args.edn_format)
    edn_output = generator.generate(cbor_data, root_type, args.annotate)
    
    # Output EDN
    if args.output:
        args.output.write_text(edn_output, encoding='utf-8')
        print(f"[OK] EDN written to: {args.output}", file=sys.stderr)
        if json_mode:
            _exit_json(diagnostics, valid=valid, root_type=checked_type, status=0,
                       features_used=features_used)
    else:
        print("\n" + "=" * 50, file=sys.stderr)
        print("EDN Output:", file=sys.stderr)
        print("=" * 50, file=sys.stderr)
        print(edn_output)

if __name__ == '__main__':
    main()
