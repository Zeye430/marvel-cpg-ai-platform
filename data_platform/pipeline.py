"""Stage 1-2 snapshot builds with quality gates and atomic Silver publication."""

import fcntl
import json
import os
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from .ingestion import digest, json_hash, load_bronze, load_contract, snapshot_inputs
from .quality import Quality
from .silver import shared_relations, source_business_checks, typed_sources

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class BuildConfig:
    source_dir: Path = ROOT / "data/raw"
    output_root: Path = ROOT / "outputs/v3/platform"
    snapshot_root: Path = ROOT / "data/raw/_snapshots/v3"
    contract_path: Path = PACKAGE / "contracts/v3/sources.json"
    calendar_start: date = date(2025, 1, 1)
    as_of: date = date(2026, 5, 31)
    memory_limit: str = "1GB"
    threads: int = 2

    def validate(self, contract):
        if self.as_of > date.fromisoformat(contract["development_cutoff"]):
            raise ValueError("June outcomes are locked; as_of must not exceed the development cutoff")
        if self.calendar_start > self.as_of:
            raise ValueError("calendar_start must be on or before as_of")
        if self.threads < 1:
            raise ValueError("threads must be positive")
        for path in (self.output_root, self.snapshot_root):
            if path.resolve() == self.source_dir.resolve():
                raise ValueError("Generated artifacts must not replace the source directory")


def now():
    return datetime.now(timezone.utc)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".pending-",
                                     encoding="utf-8", delete=False) as handle:
        pending = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(pending, path)


@contextmanager
def writer_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"Another writer holds {path}") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def code_provenance():
    files = sorted(p for p in PACKAGE.rglob("*") if p.suffix in {".py", ".json", ".sql"})
    files += [ROOT / "pyproject.toml", ROOT / "uv.lock"]
    tree_hash = json_hash([(str(p.relative_to(ROOT)), digest(p)) for p in files if p.is_file()])
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                                  capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unavailable"
    return revision, tree_hash


def build(config=None, *, publish_silver=False):
    """Return an accepted candidate or raise while retaining the failed build evidence.

    Builds contain restricted full-snapshot Bronze. They are never application serving
    databases. Publication here is specifically a Silver pipeline handoff.
    """
    config = config or BuildConfig()
    contract = load_contract(config.contract_path)
    config.validate(contract)
    with writer_lock(config.output_root / ".writer.lock"):
        return _build(config, contract, publish_silver)


def _build(config, contract, publish_silver):
    started = now()
    build_id = started.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
    run_id = "ingest-" + uuid.uuid4().hex
    build_dir = config.output_root / "builds" / build_id
    build_dir.mkdir(parents=True)
    db_path = build_dir / "build.duckdb"
    connection = duckdb.connect(str(db_path))
    connection.execute("SET memory_limit = ?", [config.memory_limit])
    connection.execute("SET threads = ?", [config.threads])
    connection.execute("SET preserve_insertion_order = true")
    connection.execute((PACKAGE / "sql/meta.sql").read_text())
    revision, tree_hash = code_provenance()
    configuration = {
        "contract_hash": digest(config.contract_path), "as_of": str(config.as_of),
        "calendar_start": str(config.calendar_start), "duckdb_version": duckdb.__version__,
        "transform_version": "silver.v1", "assortment_policy": "static_simulation_with_first_seen_flag_v1",
        "availability_policy": "explicit_same_day_simulation_assumptions_v1",
    }
    config_hash = json_hash(configuration)
    connection.execute("INSERT INTO meta.build_runs VALUES (?, NULL, ?, ?, ?, ?, ?, NULL, 'Running', NULL)",
                       [build_id, config_hash, revision, tree_hash, json.dumps(configuration), started])
    connection.execute("INSERT INTO meta.ingestion_runs VALUES (?, NULL, ?, ?, ?, NULL, 'Running', NULL)",
                       [run_id, contract["data_version"], str(config.source_dir.resolve()), started])
    quality = Quality(connection, build_id)
    result = {"build_id": build_id, "database": str(db_path.resolve()),
              "quality_report": str((build_dir / "quality.json").resolve()),
              "status": "Running", "layer": "silver", "application_ready": False,
              "configuration": configuration, "config_hash": config_hash,
              "code_revision": revision, "code_tree_hash": tree_hash}
    previous, manifest_path = None, config.output_root / "published/silver.json"
    try:
        with writer_lock(config.snapshot_root / ".snapshot.lock"):
            dataset_hash, snapshot, hashes = snapshot_inputs(config, contract, quality, run_id)
        result.update(dataset_hash=dataset_hash, snapshot=str(snapshot.resolve()))
        connection.execute("UPDATE meta.ingestion_runs SET dataset_hash = ? WHERE ingestion_run_id = ?",
                           [dataset_hash, run_id])
        connection.execute("UPDATE meta.build_runs SET dataset_hash = ? WHERE build_id = ?", [dataset_hash, build_id])
        load_bronze(connection, config, contract, quality, run_id, dataset_hash, snapshot, hashes)
        connection.execute("UPDATE meta.ingestion_runs SET status = 'Accepted', finished_at = ? WHERE ingestion_run_id = ?",
                           [now(), run_id])
        typed_sources(connection, config, contract, quality, build_id)
        source_business_checks(connection, contract, quality)
        shared_relations(connection, config, contract, quality, build_id)
        rules = connection.execute("SELECT DISTINCT rule_version FROM silver.dim_guardrails").fetchall()
        for (rule_version,) in rules:
            connection.execute("INSERT INTO meta.policy_registry VALUES ('trade_guardrails', ?, 'Requires review', NULL, NULL, ?, ?)",
                               [rule_version, "retained dim_guardrails.csv", "Discount interpretation requires approval"])
        for policy in ("same_day_end_simulation_v1", "initial_schedule_known_at_order_simulation_v1",
                       "campaign_known_at_start_simulation_v1", "static_simulation_assortment_v1"):
            connection.execute("INSERT INTO meta.policy_registry VALUES (?, '1', 'Assumed for simulation', NULL, NULL, ?, ?)",
                               [policy, "V3 architecture; no historical availability feed supplied",
                                "Production requires source availability or membership history"])
        result["silver_rows"] = {
            table: connection.execute(f'SELECT count(*) FROM silver."{table}"').fetchone()[0]
            for (table,) in connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'silver' ORDER BY table_name").fetchall()
        }
        result["receipt_reconciliation"] = dict(connection.execute(
            "SELECT reconciliation_status, count(*) FROM silver.replenishment_events GROUP BY 1"
        ).fetchall())
        result["consecutive_inventory_checks"] = connection.execute(
            "SELECT count(*) FROM silver.inventory_reconciliation WHERE is_consecutive"
        ).fetchone()[0]
        if publish_silver:
            if manifest_path.exists():
                previous = json.loads(manifest_path.read_text())
            publication_id = "silver-" + build_id
            result.update(publication_id=publication_id,
                          previous_publication_id=previous.get("publication_id") if previous else None)
            connection.execute("INSERT INTO meta.publications VALUES (?, ?, 'silver', 'Passed', ?, ?)",
                               [publication_id, build_id, result["previous_publication_id"], now()])
        connection.execute("UPDATE meta.build_runs SET status = 'Accepted', finished_at = ? WHERE build_id = ?", [now(), build_id])
        result["status"] = "Accepted"
    except Exception as error:
        result.update(status="Failed", error=f"{type(error).__name__}: {error}")
        connection.execute("UPDATE meta.build_runs SET status = 'Failed', error = ?, finished_at = ? WHERE build_id = ?",
                           [result["error"], now(), build_id])
        connection.execute("UPDATE meta.ingestion_runs SET status = 'Failed', error = ?, finished_at = ? "
                           "WHERE ingestion_run_id = ? AND status = 'Running'", [result["error"], now(), run_id])
        raise
    finally:
        atomic_json(build_dir / "quality.json", quality.report())
        connection.close()
        result["finished_at"] = str(now())
        atomic_json(build_dir / "run.json", result)
    result["database_sha256"] = digest(db_path)
    result["quality_sha256"] = digest(build_dir / "quality.json")
    atomic_json(build_dir / "run.json", result)
    if publish_silver:
        # The immutable history entry is written before the atomic pointer promotion.
        atomic_json(config.output_root / "published/history" / (result["publication_id"] + ".json"), result)
        atomic_json(manifest_path, result)
    return result
