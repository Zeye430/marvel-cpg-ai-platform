"""Byte-preserved snapshots and a strict, text-only Bronze load."""

import csv
import hashlib
import json
import re
import shutil
from pathlib import Path

from .quality import QualityError


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def load_contract(path):
    contract = json.loads(Path(path).read_text())
    sources = contract["sources"]
    names = [source["name"] for source in sources]
    if len(sources) != 8 or len(set(names)) != 8:
        raise ValueError("The V3 source contract must declare eight distinct sources")
    for source in sources:
        if not re.fullmatch(r"[a-z_]+", source["name"]):
            raise ValueError("Invalid contract source name")
        if source["filename"] != source["name"] + ".csv":
            raise ValueError("Contract filename must match its source name")
        columns = [column["name"] for column in source["columns"]]
        if len(set(columns)) != len(columns):
            raise ValueError("Duplicate contract columns")
        for column in source["columns"]:
            if not re.fullmatch(r"[a-z_]+", column["name"]):
                raise ValueError("Invalid column name")
            if not re.fullmatch(r"VARCHAR|BOOLEAN|DATE|BIGINT|DECIMAL\(\d+,\d+\)", column["type"]):
                raise ValueError("Unsupported contract type")
        for column in source["primary_key"] + source["nullable_columns"]:
            if column not in columns:
                raise ValueError("Unknown contract key or nullable column")
    return contract


def snapshot_inputs(config, contract, quality, run_id):
    """Copy before parsing; never overwrite an existing accepted input snapshot."""
    sources = contract["sources"]
    missing = [s["filename"] for s in sources if not (config.source_dir / s["filename"]).is_file()]
    quality.record("required_files", "bronze", len(missing), checked_count=8, evidence=missing)
    quality.require_pass()
    hashes = {s["filename"]: digest(config.source_dir / s["filename"]) for s in sources}
    dataset_hash = json_hash(sorted(hashes.items()))
    snapshot = config.snapshot_root / dataset_hash
    if snapshot.exists():
        bad = [name for name, sha in hashes.items()
               if not (snapshot / name).is_file() or digest(snapshot / name) != sha]
        quality.record("immutable_snapshot", "bronze", len(bad), checked_count=8, evidence=bad)
        quality.require_pass()
    else:
        pending = config.snapshot_root / (".pending-" + run_id)
        pending.mkdir(parents=True)
        for filename in hashes:
            shutil.copyfile(config.source_dir / filename, pending / filename)
        bad = [name for name, sha in hashes.items() if digest(pending / name) != sha]
        quality.record("immutable_snapshot", "bronze", len(bad), checked_count=8, evidence=bad)
        quality.require_pass()
        pending.rename(snapshot)
    return dataset_hash, snapshot, hashes


def load_bronze(connection, config, contract, quality, run_id, dataset_hash, snapshot, hashes):
    for source in contract["sources"]:
        filename, name = source["filename"], source["name"]
        path = snapshot / filename
        expected = [column["name"] for column in source["columns"]]
        count, malformed, examples = 0, 0, []
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.reader(handle, strict=True)
                header = next(reader, [])
                for count, row in enumerate(reader, 1):
                    if len(row) != len(expected):
                        malformed += 1
                        if len(examples) < 5:
                            examples.append({"source_row_number": count, "fields": len(row)})
        except (csv.Error, UnicodeError) as error:
            quality.record("csv_syntax", name, 1, evidence=[str(error)])
            raise QualityError(f"Cannot parse {filename}") from error
        quality.record("ordered_header", name, int(header != expected), checked_count=1,
                       evidence=[] if header == expected else [{"actual": header, "expected": expected}])
        quality.record("csv_field_count", name, malformed, checked_count=count, evidence=examples)
        connection.execute("INSERT INTO meta.source_files VALUES (?, ?, ?, ?, ?, ?, ?)",
                           [run_id, filename, hashes[filename], path.stat().st_size, count,
                            json.dumps(header), contract["contract_version"]])
        quality.require_pass()
        columns = ", ".join(f"{literal(column)}: 'VARCHAR'" for column in expected)
        # One parsing thread plus preserved insertion order makes row_number the CSV record number.
        # force_not_null keeps empty source strings in Bronze; Silver interprets null tokens.
        text_columns = ", ".join(literal(column) for column in expected)
        connection.execute(f"""
            CREATE TABLE bronze.{identifier(name)} AS
            SELECT *, {literal(contract['data_version'])} AS data_version,
                   {literal(dataset_hash)} AS dataset_hash,
                   {literal(filename)} AS source_file,
                   {literal(hashes[filename])} AS source_file_hash,
                   row_number() OVER () AS source_row_number,
                   {literal(run_id)} AS ingestion_run_id,
                   current_timestamp AS ingested_at
            FROM read_csv({literal(path)}, columns={{{columns}}}, header=true,
                 auto_detect=false, delim=',', quote='"', escape='"', nullstr='',
                 force_not_null=[{text_columns}],
                 parallel=false, strict_mode=true, ignore_errors=false)
        """)
        actual = connection.execute(f"SELECT count(*) FROM bronze.{identifier(name)}").fetchone()[0]
        quality.record("parse_accounting", name, abs(actual - count), checked_count=count)
        if name in {"dim_stores", "dim_products", "dim_guardrails", "fact_sell_out"}:
            quality.record("nonempty_required_source", name, int(actual == 0), checked_count=actual)
        # Reference counts/hashes are provenance, never an acceptance limit for subsequent deliveries.
        quality.record("reference_delivery", name, int(hashes[filename] != source["reference_sha256"]),
                       severity="info", note="Different input revision; reference hash is informational only.")
    quality.require_pass()
