from __future__ import annotations

import argparse
import json
import os

from .config import Settings
from .pipeline import Pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Watch EDINET activist large-shareholding filings.")
    parser.add_argument("--data-dir", default=os.getenv("DATA_DIR", "data"), help="Artifact and SQLite directory.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Fetch recent EDINET filing metadata.")
    scan.add_argument("--days", type=int, default=3, help="Rolling Tokyo-calendar-day window.")

    subparsers.add_parser("process", help="Download and parse discovered filings.")

    draft = subparsers.add_parser("draft", help="Generate LLM report JSON and Markdown draft.")
    draft.add_argument("--offline", action="store_true", help="Generate a deterministic factual draft without OpenAI.")

    email = subparsers.add_parser("email", help="Email pending generated drafts.")
    email.set_defaults(command="email")

    publish = subparsers.add_parser("publish", help="Build the static website from generated drafts.")
    publish.add_argument("--deploy", action="store_true", help="Deploy with Firebase CLI after building the site.")

    followups = subparsers.add_parser("followups", help="Run or manage monthly follow-up reports.")
    followup_subparsers = followups.add_subparsers(dest="followup_command")
    run_followups = followup_subparsers.add_parser("run", help="Run due monthly follow-up reports.")
    run_followups.add_argument("--offline", action="store_true", help="Generate deterministic follow-ups without OpenAI.")
    followup_subparsers.add_parser("list", help="List follow-up schedules.")
    for name in ("pause", "resume", "stop"):
        command = followup_subparsers.add_parser(name, help=f"{name.title()} a follow-up schedule.")
        command.add_argument("doc_id", help="Initial 350 EDINET document ID.")
    limit = followup_subparsers.add_parser("set-limit", help="Change the maximum monthly follow-up count.")
    limit.add_argument("doc_id", help="Initial 350 EDINET document ID.")
    limit.add_argument("--max-runs", type=int, required=True, help="Maximum number of monthly follow-ups.")

    run = subparsers.add_parser("run", help="Run scan, process, draft, and email.")
    run.add_argument("--days", type=int, default=3, help="Rolling Tokyo-calendar-day window.")
    run.add_argument("--offline", action="store_true", help="Generate deterministic drafts without OpenAI.")
    run.add_argument("--no-email", action="store_true", help="Skip SMTP delivery.")
    run.add_argument("--publish", action="store_true", help="Build the static website after drafting.")
    run.add_argument("--deploy", action="store_true", help="Deploy the static website with Firebase CLI after building.")
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
    elif args.command == "publish":
        result = pipeline.publish(deploy=args.deploy)
        print(json.dumps({"site_built": result.built, "site_deployed": result.deployed}, indent=2))
    elif args.command == "followups":
        if args.followup_command in (None, "run"):
            print(json.dumps({"followups": pipeline.followups(offline=getattr(args, "offline", False))}, indent=2))
        elif args.followup_command == "list":
            pipeline.initialize()
            print(json.dumps(pipeline.storage.list_followups(), ensure_ascii=False, indent=2))
        elif args.followup_command == "pause":
            pipeline.initialize()
            print(json.dumps({"updated": pipeline.storage.set_followup_status(args.doc_id, "paused")}, indent=2))
        elif args.followup_command == "resume":
            pipeline.initialize()
            print(json.dumps({"updated": pipeline.storage.set_followup_status(args.doc_id, "active")}, indent=2))
        elif args.followup_command == "stop":
            pipeline.initialize()
            print(json.dumps({"updated": pipeline.storage.set_followup_status(args.doc_id, "stopped")}, indent=2))
        elif args.followup_command == "set-limit":
            pipeline.initialize()
            print(json.dumps({"updated": pipeline.storage.set_followup_limit(args.doc_id, args.max_runs)}, indent=2))
    elif args.command == "run":
        print(
            json.dumps(
                pipeline.run(
                    args.days,
                    offline=args.offline,
                    send_email=not args.no_email,
                    publish=args.publish or args.deploy,
                    deploy=args.deploy,
                ),
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
