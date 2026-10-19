"""Read a verified serving snapshot without SQL, writes or authentication bypass.

Run from the repository root with ``python -m examples.read_shared_data --help``.
The principal argument is for trusted local development, not an HTTP auth design.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import duckdb

from data_platform.access import DataUnavailable, ServingReader
from data_platform.serving import VIEWS


DEFAULT_PUBLICATION = Path("outputs/v3/platform/delivery/serving/published/serving.json")


def open_reader(*, publication=None, artifact_dir=None):
    """Pin one local artifact; a copied artifact can be opened without old paths."""
    if publication is not None and artifact_dir is not None:
        raise ValueError("Choose a publication or an artifact directory, not both")
    if artifact_dir is not None:
        return ServingReader(Path(artifact_dir).resolve())
    pointer = Path(publication or DEFAULT_PUBLICATION).resolve()
    record = json.loads(pointer.read_text())
    directory = Path(record["directory"])
    if not directory.is_absolute():
        raise ValueError("Generated publication directories must be absolute; use --artifact-dir for a copied artifact")
    if not directory.is_dir():
        raise FileNotFoundError("Published artifact is not present here; rebuild locally or use --artifact-dir for a copied artifact")
    reader = ServingReader(directory)
    for key in ("artifact_id", "dataset_hash", "source_build_id", "as_of", "database_sha256"):
        if record.get(key) != reader.record.get(key):
            raise ValueError(f"Publication/artifact mismatch: {key}")
    return reader


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--publication", type=Path, help="Local published serving.json; defaults to the build-all delivery root")
    source.add_argument("--artifact-dir", type=Path, help="Accepted serving directory containing run.json, quality.json and business.duckdb")
    parser.add_argument("--schema-only", action="store_true", help="Inspect field names/types without reading business rows")
    parser.add_argument("--view", choices=sorted(VIEWS), default="negotiation_context")
    parser.add_argument("--principal-id", help="Explicit scoped local test identity; applications must use a verified session")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--allow-candidate-estimates",action="store_true",
                        help="Explicitly read separate candidate estimates; not operational approval")
    args = parser.parse_args(argv)
    if not args.schema_only and not args.principal_id:
        parser.error("--principal-id is required for business-row reads")
    if not 1 <= args.limit <= 10000:
        parser.error("--limit must be between 1 and 10000")
    if args.start and args.end and args.start > args.end:
        parser.error("--start must not exceed --end")
    try:
        reader = open_reader(publication=args.publication, artifact_dir=args.artifact_dir)
        result = {key: reader.record[key] for key in ("artifact_id", "dataset_hash", "as_of", "metric_view")}
        if args.schema_only:
            result["schema"] = reader.schema()
        else:
            rows = reader.read(args.view, args.principal_id, start=args.start, end=args.end, limit=args.limit,
                               allow_candidate_estimates=args.allow_candidate_estimates)
            result.update(view=args.view, row_count=len(rows), rows=rows,
                          estimate_release_status=reader.record.get("estimate_views_status") if args.view.startswith("estimated_") else None,
                          result_scope="Bounded rows, not a complete dataset or an aggregate API")
            if args.view.startswith("estimated_"):
                result["default_metric_view"] = reader.record["metric_view"]
                result["metric_view"] = "operational_estimate_candidate"
    except DataUnavailable as error:
        print(f"Data unavailable: {error}", file=sys.stderr)
        return 3
    except (OSError, ValueError, KeyError, RuntimeError, duckdb.Error) as error:
        print(f"Read failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
