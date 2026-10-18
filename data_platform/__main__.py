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
    command.add_argument("--publish-silver", action="store_true",
                         help="Promote only the validated Silver handoff, not an app serving database")
    args = vars(parser.parse_args())
    args.pop("command")
    publish = args.pop("publish_silver")
    try:
        result = build(BuildConfig(**args), publish_silver=publish)
    except Exception as error:
        print(f"Build failed; prior publication retained. {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
