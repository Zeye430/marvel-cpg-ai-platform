"""Shared platform commands; run python -m data_platform --help for usage."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .pipeline import BuildConfig, build


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the versioned shared V3 data platform")
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
    command.add_argument("--include-kpi-marts", action="store_true",
                         help="Build shared KPI marts, including their daily-state dependency")
    command.add_argument("--invoice-extract-complete", action="store_true",
                         help="Explicit simulation assumption allowing absent invoice flow to be zero")
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
    audit = commands.add_parser("audit-data", help="Compare active source hashes with V3 and a fetched Git ref")
    audit.add_argument("--source-dir", type=Path)
    audit.add_argument("--contract-path", type=Path)
    audit.add_argument("--git-ref", default="origin/main")
    audit.add_argument("--output-path", type=Path)
    handoff = commands.add_parser(
        "export-feature-input",
        help="Export allowlisted historical state to an isolated feature-input database",
    )
    handoff.add_argument("--source-build-dir", type=Path, required=True,
                         help="Accepted build directory containing run.json, quality.json and build.duckdb")
    handoff.add_argument("--output-root", type=Path,
                         default=defaults.output_root / "feature-inputs",
                         help="Parent directory for new immutable projections; no serving publication")
    handoff.add_argument("--as-of", type=date.fromisoformat, required=True,
                         help="Explicit YYYY-MM-DD input cutoff, no later than the source cutoff or 2026-05-31")
    args = vars(parser.parse_args(argv))
    action = args.pop("command")
    try:
        if action == "audit-data":
            from .pipeline import atomic_json
            from .version_audit import audit_data
            output = args.pop("output_path")
            result = audit_data(**args)
            if output:
                atomic_json(output, result)
        elif action == "evaluate-missing":
            from .masking import evaluate_missing
            result = evaluate_missing(**args)
        elif action == "export-feature-input":
            from .feature_handoff import export_feature_input
            result = export_feature_input(**args)
        else:
            publish = args.pop("publish_silver")
            result = build(BuildConfig(**args), publish_silver=publish)
    except KeyboardInterrupt:
        print("Operation interrupted; accepted publications unchanged.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Operation failed; accepted publications unchanged. {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
