from __future__ import annotations

import argparse
import json

from .config import Settings
from .pipeline import Pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Watch EDINET activist large-shareholding filings.")
    parser.add_argument("--data-dir", default="data", help="Artifact and SQLite directory.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Fetch recent EDINET filing metadata.")
    scan.add_argument("--days", type=int, default=3, help="Rolling Tokyo-calendar-day window.")

    subparsers.add_parser("process", help="Download and parse discovered filings.")

    draft = subparsers.add_parser("draft", help="Generate LLM report JSON and Markdown draft.")
    draft.add_argument("--offline", action="store_true", help="Generate a deterministic factual draft without OpenAI.")

    email = subparsers.add_parser("email", help="Email pending generated drafts.")
    email.set_defaults(command="email")

    run = subparsers.add_parser("run", help="Run scan, process, draft, and email.")
    run.add_argument("--days", type=int, default=3, help="Rolling Tokyo-calendar-day window.")
    run.add_argument("--offline", action="store_true", help="Generate deterministic drafts without OpenAI.")
    run.add_argument("--no-email", action="store_true", help="Skip SMTP delivery.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env(data_dir=args.data_dir)
    pipeline = Pipeline(settings)

    if args.command == "scan":
        print(json.dumps({"scanned": pipeline.scan(args.days)}, indent=2))
    elif args.command == "process":
        print(json.dumps({"processed": pipeline.process()}, indent=2))
    elif args.command == "draft":
        print(json.dumps({"drafted": pipeline.draft(offline=args.offline)}, indent=2))
    elif args.command == "email":
        print(json.dumps({"emailed": pipeline.email()}, indent=2))
    elif args.command == "run":
        print(json.dumps(pipeline.run(args.days, offline=args.offline, send_email=not args.no_email), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
