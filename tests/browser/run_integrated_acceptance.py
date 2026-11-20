"""Browser acceptance for the integrated Module 2 + Module 4 app (real Shiny app + headless Chrome/CDP).

Usage (repository root, app + data environment):
  python tests/browser/run_integrated_acceptance.py --out <evidence dir> [--with-postgres] [--only main ...]
  python tests/browser/run_integrated_acceptance.py --serve [--port 8000]   # keep a synthetic demo running

Builds SYNTHETIC serving artifacts (tests/postgres_fixtures; stub scoring bundle, no model fitting),
starts a deterministic loopback model stub (tests/browser/llm_stub.py) and loopback-only app
instances, then runs integrated_acceptance.mjs with the system Node and Chrome. Scenarios:
main (sign-in, both modules, navigation, scope, identity switch while answering, prediction and
ranking answers, viewports),
unavailable (no shared source), nomodel (no language model), candidate (test-double candidate
disclosure), unreleased (predictions not released; business data still answered) and, with --with-postgres and the marked disposable test database, pg / pgrevoked.
Stops only the processes it started; no cloud, client data or real model.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url, process, seconds=90):
    for _ in range(int(seconds * 5)):
        if process.poll() is not None:
            raise RuntimeError(f"Process exited early ({process.returncode}) for {url}")
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"No response from {url}")


def clean_env():
    drop = ("SHARED_DATA_", "MODULE2_", "MODULE4_", "APP_STATE_", "DATABASE_URL")
    return {k: v for k, v in os.environ.items() if not k.startswith(drop)}


def start(cmd, env, log):
    handle = open(log, "w")
    return subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


def start_app(env, out, name, entry="integrated_app.py", port=None):
    port = port or free_port()
    process = start([sys.executable, "-m", "shiny", "run", entry, "--host", "127.0.0.1", "--port", str(port)],
                    env, out / f"{name}-server.log")
    url = f"http://127.0.0.1:{port}/"
    wait_http(url, process)
    return process, url


def run_scenario(name, url, out, expect):
    done = subprocess.run(["node", str(HERE / "integrated_acceptance.mjs"), "--scenario", name, "--url", url,
                           "--out", str(out), "--manager-stores", str(expect["manager"]),
                           "--rep-stores", str(expect["rep"]), "--as-of", expect["as_of"],
                           "--artifact", expect["artifact"]],
                          cwd=ROOT, capture_output=True, text=True, timeout=1800)
    (out / f"{name}-node.log").write_text(done.stdout + done.stderr)
    return json.loads((out / f"{name}-results.json").read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--with-postgres", action="store_true")
    parser.add_argument("--only", nargs="*", help="Run only these scenarios")
    parser.add_argument("--serve", action="store_true", help="Start the synthetic demo and keep it running")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not args.serve and not args.out:
        parser.error("--out is required unless --serve is given")
    import postgres_fixtures as fx
    from data_platform.access import ServingReader

    sources = fx.synthetic_sources()
    reader = ServingReader(sources["model"])
    expect = {"manager": reader.principal_scope("demo:manager")["stores"],
              "rep": reader.principal_scope("demo:rep-a")["stores"],
              "as_of": str(reader.record["as_of"]), "artifact": reader.record["artifact_id"]}
    out = (args.out or Path(tempfile.mkdtemp(prefix="integrated-serve-"))).resolve()
    out.mkdir(parents=True, exist_ok=True)
    stub_port = free_port()
    stub = start([sys.executable, str(HERE / "llm_stub.py"), str(stub_port), "6"], clean_env(), out / "stub.log")
    wait_http(f"http://127.0.0.1:{stub_port}/v1/models", stub)
    base = {**clean_env(), "SHARED_DATA_ARTIFACT_DIR": str(sources["model"]), "MODULE4_LLM_PROVIDER": "openai",
            "MODULE4_LLM_BASE_URL": f"http://127.0.0.1:{stub_port}", "MODULE4_LLM_MODEL": "deterministic-stub",
            "MODULE4_RAG_DENSE": "0"}
    processes = [stub]
    if args.serve:
        try:
            app, url = start_app(base, out, "serve", port=args.port)
            processes.append(app)
            print(f"Synthetic integrated demo at {url} (model stub on {stub_port}); Ctrl+C stops it.", flush=True)
            app.wait()
        except KeyboardInterrupt:
            pass
        finally:
            for process in processes:
                stop(process)
        return 0

    summary = {"synthetic_artifact": expect["artifact"], "expected_scope": expect, "scenarios": {}}
    db = variants = None
    try:
        missing = Path(tempfile.gettempdir()) / "integrated-acceptance-missing" / "serving.json"
        from test_module4_predictions import variant
        variants = tempfile.TemporaryDirectory(prefix="integrated-unreleased-")   # synthetic copy, not evidence
        unreleased = str(variant(sources["model"], Path(variants.name), status="Unavailable_model_not_released"))
        plans = [("main", base, "integrated_app.py", None),
                 ("unavailable", {**{k: v for k, v in base.items() if k != "SHARED_DATA_ARTIFACT_DIR"},
                                  "SHARED_DATA_PUBLICATION": str(missing)}, "integrated_app.py", None),
                 ("nomodel", {**base, "MODULE4_LLM_BASE_URL": f"http://127.0.0.1:{free_port()}"},
                  "integrated_app.py", None),
                 ("candidate", base, "tests/browser/candidate_disclosure_app.py", None),
                 ("slow", {**base, "SLOW_SCOPE_SECONDS": "4"}, "tests/browser/slow_scope_app.py", None),
                 ("unreleased", {**base, "SHARED_DATA_ARTIFACT_DIR": unreleased}, "integrated_app.py", None)]
        if args.with_postgres:
            fx.require_database()
            from data_platform import postgres_publisher as pub
            db = fx.TestDatabase()
            pub.initialize_postgres(db.publisher, apply=True)
            loaded = pub.load_postgres_candidate(sources["model"], db.publisher, apply=True)
            pub.activate_postgres_release(db.publisher, loaded["release_id"], apply=True)
            temp = Path(tempfile.mkdtemp(prefix="integrated-scope-"))

            def scopes(assignments, version):
                path = temp / f"scope-{version}.json"
                fx.scope_file(path, assignments, version)
                pub.configure_postgres_scopes(db.publisher, path, apply=True)
            scopes(sources["scope"]["assignments"], "demo-v1")
            revoke = [r for r in sources["scope"]["assignments"] if r["principal_id"] != "demo:rep-a"]
            pg_env = {**{k: v for k, v in base.items() if k != "SHARED_DATA_ARTIFACT_DIR"},
                      "SHARED_DATA_BACKEND": "postgres", "SHARED_DATA_DATABASE_URL": db.app_url,
                      "SHARED_DATA_LOCAL_TEST": "1"}
            plans.append(("pg", pg_env, "integrated_app.py", lambda: scopes(revoke, "rep-a-revoked")))
        for name, env, entry, after in plans:
            if args.only and name not in args.only:
                continue
            app, url = start_app(env, out, name, entry)
            processes.append(app)
            try:
                result = run_scenario(name, url, out, expect)       # exactly one browser run per scenario
                summary["scenarios"][name] = {k: result[k] for k in ("passed", "failed", "browser")}
                if after is not None:                              # same app process: revoke, then re-check
                    after()
                    result = run_scenario("pgrevoked", url, out, expect)
                    summary["scenarios"]["pgrevoked"] = {k: result[k] for k in ("passed", "failed", "browser")}
            finally:
                stop(app)
    finally:
        for process in processes:
            stop(process)
        if db is not None:
            db.drop()
        if variants is not None:
            variants.cleanup()
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0 if summary["scenarios"] and all(s["failed"] == 0 for s in summary["scenarios"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
