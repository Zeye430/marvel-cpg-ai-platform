"""Drive the Module 2 browser acceptance (real Shiny app + headless Chrome/CDP).

Usage (repository root, full environment):
  .venv-module2/bin/python tests/browser/run_acceptance.py --candidate-root <deliver output root> --out <evidence dir>

Starts loopback-only demo app instances, prepares honest state fixtures from
the same synthetic candidate, runs tests/browser/cdp_acceptance.mjs with the
system Node and Chrome, and stops only the processes it started.
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).with_name("cdp_acceptance.mjs")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_app(serving_root, env=None):
    port = free_port()
    process = subprocess.Popen([sys.executable, "-m", "modules.module_2", "demo", "--serving-root", str(serving_root),
                                "--port", str(port)], cwd=ROOT, env={**os.environ, **(env or {})},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    url = f"http://127.0.0.1:{port}/"
    for _ in range(100):
        try:
            urllib.request.urlopen(url, timeout=1)
            return process, url
        except OSError:
            time.sleep(0.2)
    process.terminate()
    raise RuntimeError("Demo app did not start")


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


EXPECT = []


def node(scenario, url, out, state=None, watcher=None):
    command = ["node", str(SCRIPT), "--scenario", scenario, "--url", url, "--out", str(out), *EXPECT]
    if state:
        command += ["--state", state]
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    while process.poll() is None:
        if watcher:
            watcher()
        time.sleep(0.2)
    output = process.stdout.read()
    (Path(out) / "node.log").write_text(output)
    return process.returncode, output.strip().splitlines()[-1] if output.strip() else ""


def expectations(candidate):
    """Scope sizes, an out-of-scope store and synthetic labelling from the candidate itself."""
    scope = json.loads((candidate / "governance" / "demo-scope.json").read_text())["assignments"]
    rep_a = sorted(a["store_id"] for a in scope if a["principal_id"] == "demo:rep-a")
    manager = sorted(a["store_id"] for a in scope if a["principal_id"] == "demo:manager")
    outside = next(s for s in reversed(manager) if s not in rep_a)
    synthetic = json.loads((candidate / "config.json").read_text()).get("synthetic", False)
    EXPECT[:] = ["--rep-stores", str(len(rep_a)), "--manager-stores", str(len(manager)),
                 "--outside-store", json.dumps(outside), "--synthetic", "1" if synthetic else "0"]


def unreleased_root(candidate_root, scratch):
    """A real base serving snapshot (no model outputs) with the demo scope applied."""
    from data_platform.serving import build_serving
    config = json.loads((candidate_root / "config.json").read_text())
    workflow = json.loads(Path(config["workflow_run"]).read_text())
    source = Path(workflow["components"]["base"]["database"]).parent
    features = workflow["components"]["scoring_features"]["directory"]
    root = scratch / "unreleased-root"
    build_serving(source, features, root, access_policy=candidate_root / "governance" / "demo-scope.json", publish=True,
                  threads=2)
    return root


def error_root(candidate_root, scratch):
    """A copy of the candidate whose database can be removed mid-session."""
    pointer = json.loads((candidate_root / "serving" / "published" / "serving.json").read_text())
    source = Path(pointer["directory"])
    root = scratch / "error-root"
    target = root / "builds" / source.name
    shutil.copytree(source, target)
    (root / "published").mkdir(parents=True)
    (root / "published" / "serving.json").write_text(json.dumps({**pointer, "directory": str(target)}))
    return root, target / "business.duckdb"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    candidate = args.candidate_root.resolve()
    expectations(candidate)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    summary = {"candidate_root": str(candidate), "runs": []}
    scratch = Path(tempfile.mkdtemp(prefix="m2-browser-"))
    try:
        process, url = start_app(candidate / "serving")
        try:
            code, line = node("main", url, out / "main")
        finally:
            stop(process)
        summary["runs"].append({"scenario": "main", "exit": code, "summary": line})
        states = {
            "unavailable": (scratch / "empty-root", {}),
            "unreleased": (unreleased_root(candidate, scratch), {}),
            "stale": (candidate / "serving", {"MODULE2_LATEST_SOURCE_DATE": "2026-07-03"}),
        }
        (scratch / "empty-root").mkdir(exist_ok=True)
        for name, (root, env) in states.items():
            process, url = start_app(root, env)
            try:
                code, line = node("state", url, out / f"state-{name}", state=name)
            finally:
                stop(process)
            summary["runs"].append({"scenario": f"state:{name}", "exit": code, "summary": line})
        root, database = error_root(candidate, scratch)
        process, url = start_app(root)
        trigger = out / "state-error" / "trigger-error"

        def watcher():
            if trigger.exists() and database.exists():
                database.unlink()
        try:
            code, line = node("state", url, out / "state-error", state="error", watcher=watcher)
        finally:
            stop(process)
        summary["runs"].append({"scenario": "state:error", "exit": code, "summary": line})
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    summary["passed"] = all(r["exit"] == 0 for r in summary["runs"])
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
