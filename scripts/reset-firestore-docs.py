#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path


DEFAULT_COLLECTIONS = ("filings", "filing_history", "drafts", "followups")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Delete selected EDINET watcher documents from Firestore.",
    )
    parser.add_argument(
        "doc_ids",
        nargs="*",
        help="EDINET document IDs to delete. Also accepts one ID per line from --doc-id-file.",
    )
    parser.add_argument(
        "--doc-id-file",
        type=Path,
        help=(
            "File containing EDINET document IDs, one per line. "
            "Defaults to last seven-day run IDs when no positional IDs are supplied."
        ),
    )
    parser.add_argument(
        "--project",
        default=os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT_ID") or "activists-edinet",
        help="Google Cloud project ID.",
    )
    parser.add_argument(
        "--prefix",
        default=os.getenv("FIRESTORE_PREFIX", "edinet_watcher"),
        help="Firestore collection prefix.",
    )
    parser.add_argument(
        "--collection",
        action="append",
        choices=DEFAULT_COLLECTIONS,
        help="Collection suffix to delete from. May be repeated. Defaults to all reset collections.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete documents. Without this flag, only prints the deletion plan.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    doc_id_file = args.doc_id_file
    if doc_id_file is None and not args.doc_ids:
        doc_id_file = Path(__file__).with_name("last-seven-day-docs.txt")
    doc_ids = (_read_doc_ids(doc_id_file) if doc_id_file else []) + args.doc_ids
    doc_ids = _dedupe(doc_ids)
    collections = tuple(args.collection or DEFAULT_COLLECTIONS)

    if not doc_ids:
        raise SystemExit("No document IDs supplied.")

    paths = [
        f"{args.prefix}_{collection}/{doc_id}"
        for collection in collections
        for doc_id in doc_ids
    ]

    mode = "DELETE" if args.execute else "DRY RUN"
    print(f"{mode}: project={args.project} docs={len(doc_ids)} paths={len(paths)}")
    for path in paths:
        print(path)

    if not args.execute:
        print("\nNo changes made. Re-run with --execute to delete these documents.")
        return 0

    from google.cloud import firestore

    client = firestore.Client(project=args.project)
    for collection in collections:
        ref = client.collection(f"{args.prefix}_{collection}")
        for doc_id in doc_ids:
            ref.document(doc_id).delete()

    print(f"\nDeleted {len(paths)} Firestore document paths.")
    return 0


def _read_doc_ids(path: Path) -> list[str]:
    if not path.exists():
        return []
    ids = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            ids.append(value)
    return ids


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
