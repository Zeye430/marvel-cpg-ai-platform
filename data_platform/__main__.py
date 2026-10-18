"""Run with: uv run python -m data_platform build --publish-silver."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .pipeline import BuildConfig, build


def main():
    parser = argparse.ArgumentParser(description="Build the shared V3 Bronze/Silver foundation")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("build", help="Build and validate a new immutable candidate")
    defaults = BuildConfig()
    for flag, default in (("source-dir", defaults.source_dir), ("output-root", defaults.output_root),
                          ("snapshot-root", defaults.snapshot_root), ("contract-path", defaults.contract_path)):
        command.add_argument("--" + flag, type=Path, default=default)
    command.add_argument("--as-of", type=date.fromisoformat, default=defaults.as_of)
    command.add_argument("--calendar-start", type=date.fromisoformat, default=defaults.calendar_start)
    command.add_argument("--memory-limit", default=defaults.memory_limit)
    command.add_argument("--threads", type=int, default=defaults.threads)
    command.add_argument("--include-daily-state", action="store_true",
                         help="Also build the candidate shared Gold state; no model/app release")
    command.add_argument("--publish-silver", action="store_true",
                         help="Promote only the validated Silver handoff, not an app serving database")
    evaluation = commands.add_parser("evaluate-missing", help="Recompute March/April account-day masking diagnostics")
    evaluation.add_argument("--source-manifest", type=Path)
    evaluation.add_argument("--output-root", type=Path)
    evaluation.add_argument("--start", type=date.fromisoformat, default=date(2026, 3, 1))
    evaluation.add_argument("--end", type=date.fromisoformat, default=date(2026, 4, 30))
    evaluation.add_argument("--per-length", type=int, default=100)
    evaluation.add_argument("--seed", type=int, default=650)
    evaluation.add_argument("--memory-limit", default="1GB")
    args = vars(parser.parse_args())
    action = args.pop("command")
    try:
        if action == "evaluate-missing":
            from .masking import evaluate_missing
            result = evaluate_missing(**args)
        else:
            publish = args.pop("publish_silver")
            result = build(BuildConfig(**args), publish_silver=publish)
    except Exception as error:
        print(f"Operation failed; accepted publications unchanged. {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
