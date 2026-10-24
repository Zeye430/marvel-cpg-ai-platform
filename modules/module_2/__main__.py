"""Module 2 command line: python -m modules.module_2 <command> --help.

help/doctor never train, open labels, touch the network or write pointers.
All inputs are explicit paths; no command picks the "newest" directory.
"""

import argparse
import json
import sys
from pathlib import Path


def _print(value):
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m modules.module_2", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    c = commands.add_parser("doctor", help="Read-only environment, disk and input readiness check")
    c.add_argument("--workflow-run", type=Path)
    c.add_argument("--output-root", type=Path)
    c = commands.add_parser("authority", help="Transcribe a genuine owner approval into owner_execution_policy.v1")
    c.add_argument("--output", type=Path, required=True)
    c.add_argument("--workflow-run", type=Path, required=True)
    c.add_argument("--approver", required=True)
    c.add_argument("--approved-at", required=True, help="Timezone-aware ISO time of the owner's actual approval")
    c.add_argument("--message", required=True, help="The owner's verbatim approval text")
    c.add_argument("--message-reference", required=True)
    c.add_argument("--allowed-output-root", type=Path, action="append", required=True)
    c.add_argument("--ledger", type=Path, required=True)
    c.add_argument("--known-history", required=True)
    c.add_argument("--prior-exposures", type=Path, required=True, help="JSON list of known prior test-window exposures")
    c.add_argument("--approved-documents", type=Path, help="JSON list of approved document revisions/hashes")
    c = commands.add_parser("deliver", help="Run the approved chain once into a NEW output root")
    c.add_argument("--profile", choices=("synthetic", "v3"), required=True)
    c.add_argument("--output-root", type=Path, required=True)
    c.add_argument("--authority-file", type=Path, help="Required for v3; synthetic creates a labelled synthetic policy")
    c.add_argument("--workflow-run", type=Path, help="v3 only; defaults to the pinned accepted rolling workflow")
    for name, text in (("benchmark", "Run the Optuna benchmark in a run directory with exported development inputs"),
                       ("freeze", "Final-fit and freeze the development winner"),
                       ("validate", "Evaluate the frozen bundle once on outer validation")):
        c = commands.add_parser(name, help=text)
        c.add_argument("--run-dir", type=Path, required=True)
        c.add_argument("--workflow-run", type=Path, required=True)
        if name == "benchmark":
            c.add_argument("--profile", choices=("synthetic", "v3"), required=True)
            c.add_argument("--run-id", required=True)
    c = commands.add_parser("evaluate-holdout", help="One holdout_confirmation through the shared gate and ledger")
    c.add_argument("--run-dir", type=Path, required=True)
    c.add_argument("--workflow-run", type=Path, required=True)
    c.add_argument("--authority-file", type=Path, required=True)
    c = commands.add_parser("score", help="Label-free latest-day scoring with a frozen bundle")
    c.add_argument("--workflow-run", type=Path, required=True)
    c.add_argument("--bundle-dir", type=Path, required=True)
    c.add_argument("--output-dir", type=Path, required=True)
    c = commands.add_parser("rank", help="visit_priority.v1 ranking for one scoring package")
    c.add_argument("--scoring-dir", type=Path, required=True)
    c.add_argument("--base-serving-dir", type=Path, required=True)
    c.add_argument("--output-dir", type=Path, required=True)
    c = commands.add_parser("build-serving-candidate", help="Score/rank/monitor and publish into the run's isolated root")
    c.add_argument("--run-dir", type=Path, required=True)
    c.add_argument("--workflow-run", type=Path, required=True)
    c.add_argument("--label", required=True, help="New package label (e.g. a date); existing packages are immutable")
    c = commands.add_parser("monitor", help="Show the latest monitoring package summary of a run")
    c.add_argument("--run-dir", type=Path, required=True)
    c = commands.add_parser("daily", help="Idempotent daily batch for a new accepted source; never trains")
    c.add_argument("--run-dir", type=Path, required=True)
    c.add_argument("--workflow-run", type=Path, required=True)
    c = commands.add_parser("challenger-request", help="Show the latest challenger request, if any")
    c.add_argument("--run-dir", type=Path, required=True)
    c = commands.add_parser("rollback-candidate", help="Re-publish an earlier verified candidate in an isolated root")
    c.add_argument("--serving-root", type=Path, required=True)
    c.add_argument("--publication-id", required=True)
    c = commands.add_parser("demo", help="Run the standalone local Module 2 demo app (serving-only)")
    c.add_argument("--serving-root", type=Path, required=True)
    c.add_argument("--port", type=int, default=8765)
    c.add_argument("--host", default="127.0.0.1")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    command = args.command
    from . import delivery
    if command == "doctor":
        report = delivery.doctor(args.workflow_run, args.output_root)
        _print(report)
        return 0 if report["ok"] else 1
    from .contracts import Module2Error, read_json
    from . import inputs
    try:
        if command == "authority":
            from . import governance
            documents = json.loads(args.approved_documents.read_text()) if args.approved_documents else []
            policy = governance.create_policy(
                args.output, human_authority={"approver": args.approver, "approved_at": args.approved_at,
                                              "message": args.message, "message_reference": args.message_reference,
                                              "approved_documents": documents, "transcribed_by": "engineer (transcription only)"},
                workflow=inputs.load_workflow(args.workflow_run), allowed_output_roots=args.allowed_output_root,
                ledger_path=args.ledger, known_history=args.known_history,
                prior_exposures=json.loads(args.prior_exposures.read_text()))
            _print({"policy_id": policy["policy_id"], "output": str(args.output)})
        elif command == "deliver":
            _print(delivery.deliver(profile=args.profile, output_root=args.output_root,
                                    authority_file=args.authority_file, workflow_run=args.workflow_run))
        elif command == "benchmark":
            from . import benchmark
            workflow = inputs.load_workflow(args.workflow_run)
            if not (args.run_dir / "inputs" / "development" / "manifest.json").exists():
                inputs.export_development(workflow, args.run_dir / "inputs" / "development")
            report = benchmark.run_benchmark(args.run_dir, profile=args.profile, run_id=args.run_id)
            _print({"status": report["status"], "selection": report["selection"]})
        elif command == "freeze":
            from . import bundles
            bundle = bundles.freeze_winner(args.run_dir, inputs.load_workflow(args.workflow_run))
            _print({"model_version": bundle["model_version"], "frozen_at": bundle["frozen_at"]})
        elif command == "validate":
            from . import evaluation
            report = evaluation.validate(args.run_dir, inputs.load_workflow(args.workflow_run))
            _print({"numeric_gate_passed": report["numeric_gate_passed"], "overall": report["results"][0]})
        elif command == "evaluate-holdout":
            from . import evaluation
            report = evaluation.run_holdout(args.run_dir, inputs.load_workflow(args.workflow_run), args.authority_file)
            _print({"numeric_gate_passed": report["numeric_gate_passed"], "overall": report["results"][0]})
        elif command == "score":
            from . import scoring
            _print(scoring.score(inputs.load_workflow(args.workflow_run), args.bundle_dir, args.output_dir))
        elif command == "rank":
            import duckdb
            from data_platform.artifacts import verified_artifact
            from . import ranking
            _, database, _ = verified_artifact(args.base_serving_dir, kind="serving")
            with duckdb.connect(str(database), read_only=True) as con:
                products = con.execute("SELECT sku_id,list_price,is_msl FROM gold.dim_products").df()
                stores = con.execute("SELECT store_id FROM gold.dim_stores").df()
            _print(ranking.rank(args.scoring_dir, products, stores, args.output_dir))
        elif command == "build-serving-candidate":
            config = read_json(args.run_dir / "config.json", "run config")
            _print(delivery.serving_candidate(args.run_dir, inputs.load_workflow(args.workflow_run),
                                              synthetic=bool(config.get("synthetic")), label=args.label))
        elif command == "monitor":
            packages = sorted((args.run_dir / "monitoring").glob("*/report.json"))
            _print(read_json(packages[-1]) if packages else {"status": "No_monitoring_package"})
        elif command == "daily":
            _print(delivery.daily(run_dir=args.run_dir, workflow_run=args.workflow_run))
        elif command == "challenger-request":
            _print(delivery.challenger_request(args.run_dir))
        elif command == "rollback-candidate":
            from data_platform.serving import rollback_serving
            root = args.serving_root.resolve()
            if not (root / "candidate-root.json").exists():
                raise Module2Error("Rollback is limited to isolated Module 2 candidate roots")
            _print(rollback_serving(root, args.publication_id))
        elif command == "demo":
            from .demo_app import run
            run(args.serving_root, host=args.host, port=args.port)
    except (Module2Error, ValueError, PermissionError, RuntimeError, OSError) as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
