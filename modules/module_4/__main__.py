"""
Command line for the Module 4 copilot.

    uv run --extra module4 python -m modules.module_4 status
    uv run --extra module4 python -m modules.module_4 ask "Which region had the lowest MSL compliance this quarter?"
    uv run --extra module4 python -m modules.module_4 ask "How often should I visit a Tier 2 store?" --principal demo:rep-a --role sales
    uv run --extra module4 python -m modules.module_4 eval --out outputs/v3/module4/eval

The principal and role here are trusted local testing inputs (like the shared-data CLI);
the app always takes them from the signed-in session.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent import Copilot


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m modules.module_4", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show model, data and knowledge-base readiness")
    ask = sub.add_parser("ask", help="answer one question")
    ask.add_argument("question")
    ask.add_argument("--principal", default="demo:manager")
    ask.add_argument("--role", default="manager", choices=["sales", "manager"])
    ev = sub.add_parser("eval", help="run the 20-question practice set (+5 safety checks)")
    ev.add_argument("--principal", default="demo:manager")
    ev.add_argument("--role", default="manager", choices=["sales", "manager"])
    ev.add_argument("--out", type=Path, default=Path("outputs/v3/module4/eval"))
    ev.add_argument("--no-safety", action="store_true")
    args = parser.parse_args(argv)

    copilot = Copilot.from_env()
    if args.command == "status":
        print(json.dumps(copilot.status(), indent=2, default=str))
        return 0
    if args.command == "ask":
        answer = copilot.ask(args.question, args.principal, args.role)
        print(f"[{answer.route} · {answer.status} · {answer.elapsed_s:.1f}s]\n\n{answer.answer}\n")
        if answer.sql:
            print("SQL:\n" + answer.sql + "\n")
        if answer.frame is not None and len(answer.frame):
            print(answer.frame.head(20).to_string(index=False) + "\n")
        for c in answer.citations:
            print(f"[{c['marker']}] {c['citation']}")
        return 0
    from .evaluation import markdown_report, run
    report = run(copilot, principal=args.principal, role=args.role, include_safety=not args.no_safety)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "eval_report.json").write_text(json.dumps(report, indent=2, default=str))
    (args.out / "eval_report.md").write_text(markdown_report(report))
    s = report["summary"]
    print(f"\nCore {s['core_passed']}/{s['core_total']} (NLQ {s['nlq_passed']}/10, RAG {s['rag_passed']}/10); "
          f"safety {s['safety_passed']}/{s['safety_total']}. Report: {args.out / 'eval_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
