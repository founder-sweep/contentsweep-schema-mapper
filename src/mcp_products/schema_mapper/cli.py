from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from .contracts import validate_record
from .engine import (
    approve_mapping,
    check_drift,
    generate_python,
    infer_schema,
    map_schema,
    resolve_mapping,
    transform_file,
    validate_mapping,
)
from .parsing import dataset, load_document
from .review import review_mapping
from .serialization import MAX_BYTES, dumps


def write_atomic(path: Path, text: str) -> None:
    """Replace only after complete validation; failures leave existing output."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("output exceeds 4 MiB limit")
    if path.is_symlink():
        raise ValueError("symbolic link output is unsupported")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _scope_args(parser: argparse.ArgumentParser) -> None:
    for name in ("user", "vendor", "domain"):
        parser.add_argument(f"--{name}", required=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="schema-map")
    sub = parser.add_subparsers(dest="command", required=True)
    infer = sub.add_parser("infer")
    infer.add_argument("source")
    infer.add_argument("--out", type=Path)
    proposal = sub.add_parser("map")
    proposal.add_argument("source")
    proposal.add_argument("--target", required=True, type=Path)
    proposal.add_argument("--out", type=Path)
    proposal.add_argument("--python", type=Path, help="requires approved mapping; use approve then generate")
    schema_proposal = sub.add_parser("propose", help="schema-only mapping; no data rows needed")
    schema_proposal.add_argument("source_schema", type=Path)
    schema_proposal.add_argument("--target", required=True, type=Path)
    schema_proposal.add_argument("--out", type=Path)
    for name in ("review", "resolve", "approve", "generate", "drift", "transform", "validate"):
        command = sub.add_parser(name)
        command.add_argument("mapping", type=Path)
        if name == "validate":
            command.add_argument("target", type=Path, nargs="?")
            command.add_argument("--records", type=Path, help="validate produced records against target")
        elif name in {"drift", "transform"}:
            command.add_argument("source", type=Path)
            _scope_args(command)
        elif name == "approve":
            command.add_argument("--reviewer", required=True)
            command.add_argument("--expect", required=True, help="mapping fingerprint shown during review")
            _scope_args(command)
        elif name == "resolve":
            command.add_argument("--decisions", required=True, type=Path,
                                 help='JSON object of target:source; null explicitly skips optional target')
        if name in {"resolve", "approve", "generate", "transform"}:
            command.add_argument("--out", required=True, type=Path)
        if name == "transform":
            command.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "infer":
            value = infer_schema(args.source)
        elif args.command in {"map", "propose"}:
            source = infer_schema(args.source) if args.command == "map" else load_document(args.source_schema)
            value = map_schema(source, load_document(args.target))
            if value["status"] == "unsupported":
                raise ValueError("unsupported contract: " + dumps(value["unsupported_contracts"]))
            if getattr(args, "python", None):
                raise ValueError("map --python now requires explicit review; use review, approve, then generate")
        else:
            mapping = load_document(args.mapping)
            if args.command == "review":
                value = review_mapping(mapping)
            elif args.command == "resolve":
                value = resolve_mapping(mapping, load_document(args.decisions))
            elif args.command == "approve":
                if args.expect != mapping["mapping_fingerprint"]:
                    raise ValueError("reviewed fingerprint differs from artifact")
                value = approve_mapping(mapping, reviewer=args.reviewer, scope={
                    name: getattr(args, name) for name in ("user", "vendor", "domain")})
            elif args.command == "generate":
                write_atomic(args.out, generate_python(mapping))
                print(dumps({"generated": str(args.out), "runtime": mapping["engine_version"]}))
                return 0
            elif args.command in {"drift", "transform"}:
                scope = {name: getattr(args, name) for name in ("user", "vendor", "domain")}
                if args.command == "drift":
                    value = check_drift(mapping, infer_schema(args.source), scope=scope)
                    print(dumps(value))
                    return 0 if value["compatible"] else 2
                protected = {args.mapping.resolve(), args.source.resolve()}
                if args.out.resolve() in protected or args.receipt and (
                    args.receipt.resolve() in protected or args.receipt.resolve() == args.out.resolve()
                ):
                    raise ValueError("output and receipt must be distinct from each other, source and mapping")
                result = transform_file(args.source, mapping, scope=scope)
                text = "".join(dumps(record) + "\n" for record in result["records"])
                write_atomic(args.out, text)
                if args.receipt:
                    write_atomic(args.receipt, dumps(result["receipt"]) + "\n")
                print(dumps(result["receipt"]))
                return 0
            else:
                target = load_document(args.target) if args.target else mapping["target"]
                value = validate_mapping(mapping, target)
                if args.records and value["valid"]:
                    count = 0
                    for count, record in enumerate(dataset(args.records).records(), 1):
                        validate_record(record, target)
                    if not count:
                        raise ValueError("produced dataset has no records")
                    value["validated_records"] = count
                print(dumps(value))
                return 0 if value["valid"] else 2
        if getattr(args, "out", None):
            write_atomic(args.out, dumps(value) + "\n")
        else:
            print(dumps(value))
        return 0
    except (ValueError, TypeError, OSError, KeyError) as exc:
        print(dumps({"error": str(exc), "status": "error"}), file=sys.stderr)
        return 2
