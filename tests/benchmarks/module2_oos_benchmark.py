"""Module 2 out-of-stock analytics benchmark on a generated SYNTHETIC delivery (not a latency SLA).

Builds a synthetic serving release (default ~225 stores x 16 SKUs), publishes it to the disposable
PostgreSQL test server named by SHARED_POSTGRES_TEST_DATABASE_URL (marker-checked, as the integration
tests do) and measures Module2Service.get_oos_analytics on DuckDB and PostgreSQL for a full-scope
manager and a Sales principal: cold (new service) and warm (cached) times, rows/pages read, Python
allocation peak and process max RSS. Prints one JSON document.

Usage (repository root):
  SHARED_POSTGRES_TEST_DATABASE_URL=... python tests/benchmarks/module2_oos_benchmark.py [--stores-per-segment 25] [--skus 16]
"""

import argparse
import json
import platform
import resource
import sys
import tempfile
import time
import tracemalloc
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]


def build(root, stores_per_segment, skus):
    import duckdb
    import test_module2_serving as m2
    from data_platform.artifacts import verified_artifact
    from data_platform.model_outputs import build_serving_with_model_outputs
    from data_platform.workflow import build_all
    from modules.module_2 import inputs, ranking, scoring, synthetic
    from modules.module_2.contracts import write_json
    from modules.module_2.service import demo_scope
    synthetic.generate(root / "raw", stores_per_segment=stores_per_segment, skus=skus)
    write_json(root / "scope0.json", {"version": "none", "assignments": []})
    build_all(date(2026, 6, 30), root / "workflow", source_dir=root / "raw", snapshot_root=root / "snapshots",
              access_policy=root / "scope0.json", rolling_evaluation=True, assume_complete_snapshot=True, threads=2,
              memory_limit="2GB")
    workflow = inputs.load_workflow(next((root / "workflow" / "runs").glob("*/run.json")))
    base = workflow["components"]["serving"]["directory"]
    m2.EVIDENCE["default"] = m2.make_evidence(workflow["plan"].to_dict())
    with patch.object(scoring.bundles, "load_bundle", lambda d: m2.StubBundle(d)):
        scoring.score(workflow, root / "bundle", root / "scoring", synthetic=True)
    _, db, _ = verified_artifact(base, kind="serving")
    with duckdb.connect(str(db), read_only=True) as con:
        products = con.execute("SELECT sku_id,list_price,is_msl FROM gold.dim_products").df()
        stores = con.execute("SELECT store_id FROM gold.dim_stores").df()
    ranking.rank(root / "scoring", products, stores, root / "ranking")
    m2.health_package(root / "health")
    scope = demo_scope(sorted(stores["store_id"]))
    write_json(root / "scope.json", scope)
    published = build_serving_with_model_outputs(
        base, root / "scoring", root / "ranking", root / "candidate", health_package_dir=root / "health",
        access_policy=root / "scope.json", publish=True, publication_kind="Synthetic_demo")
    return published["directory"], root / "scope.json", len(stores)


def measure(make_service, principal_id):
    from modules.module_2.service import Principal
    principal = Principal(principal_id, True)
    out = {}
    for measure_name in ("current", "28d"):
        service = make_service()
        tracemalloc.start()
        t0 = time.perf_counter()
        cold = service.get_oos_analytics(principal, {}, measure_name)
        cold_s = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        t0 = time.perf_counter()
        warm = service.get_oos_analytics(principal, {}, measure_name)
        warm_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        overview = service.get_priorities(principal, {"view": "all"}, page_size=10)
        overview_s = time.perf_counter() - t0
        totals = cold.detail.get("totals", {})
        out[measure_name] = {"status": cold.status, "warm_status": warm.status, "cold_seconds": round(cold_s, 3),
                             "warm_seconds": round(warm_s, 4), "rows_read": totals.get("rows"),
                             "pages": totals.get("pages"), "oos_observations": totals.get("oos"),
                             "stores_in_scope": cold.detail.get("stores_in_filter"),
                             "python_alloc_peak_mb": round(peak / 2**20, 1),
                             "overview_after_aggregate_seconds": round(overview_s, 3),
                             "overview_status": overview.status}
        if hasattr(service.reader, "close"):
            service.reader.close()
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stores-per-segment", type=int, default=25)
    parser.add_argument("--skus", type=int, default=16)
    args = parser.parse_args()
    import postgres_fixtures as fx
    from data_platform import postgres_publisher as pub
    from data_platform.postgres_reader import PostgresServingReader
    from modules.module_2.service import Module2Service
    report = {"environment": {"python": platform.python_version(), "platform": platform.platform(),
                              "machine": platform.machine()},
              "method": "Each cold run uses a new Module2Service (fresh reader/connection pool for PostgreSQL); "
                        "warm = the same call again on that service (completed-aggregate cache, scope rechecked). "
                        "Local disposable PostgreSQL in Docker on the same machine; synthetic data only."}
    with tempfile.TemporaryDirectory(prefix="m2-bench-") as temp:
        root = Path(temp)
        t0 = time.perf_counter()
        artifact, scope_path, n_stores = build(root, args.stores_per_segment, args.skus)
        report["fixture"] = {"stores": n_stores, "skus": args.skus, "build_seconds": round(time.perf_counter() - t0, 1)}
        report["duckdb"] = {pid: measure(lambda: Module2Service(artifact), pid) for pid in ("demo:manager", "demo:rep-a")}
        db = fx.TestDatabase()
        try:
            pub.initialize_postgres(db.publisher, apply=True)
            result = pub.load_postgres_candidate(artifact, db.publisher, apply=True)
            pub.activate_postgres_release(db.publisher, result["release_id"], apply=True)
            pub.configure_postgres_scopes(db.publisher, scope_path, apply=True)
            report["postgres"] = {pid: measure(lambda: Module2Service(reader=PostgresServingReader(db.app)), pid)
                                  for pid in ("demo:manager", "demo:rep-a")}
        finally:
            db.drop()
    report["process_max_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 1)  # macOS: bytes
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
