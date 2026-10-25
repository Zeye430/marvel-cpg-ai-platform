"""Read the published shared release from PostgreSQL with the same scoped reader API.

Run from the repository root (requires the ``postgres`` extra):

    export SHARED_DATA_DATABASE_URL='postgresql://<read-only user>@<host>:<port>/<db>?sslmode=verify-full'
    export SHARED_DATA_SSLROOTCERT=/path/to/project-ca.pem   # the service's CA certificate
    python -m examples.read_postgres_data --schema-only
    python -m examples.read_postgres_data --principal-id <id> --view store_day --start 2026-06-01 --end 2026-06-30

Put the password in the URL only through your secret store/environment, never in
Git or on the command line. The principal argument is for trusted local checks;
applications pass the identity from their verified session. Optional
``SHARED_DATA_RELEASE_ID`` pins an earlier *published* release.
"""

import argparse
import json
import sys
from datetime import date

from data_platform.access import DataUnavailable
from data_platform.postgres_config import ConfigError
from data_platform.readers import open_configured_reader
from data_platform.serving import VIEWS

IDENTITY = ("backend", "release_id", "publication_id", "source_artifact_id", "dataset_hash", "as_of",
            "metric_view", "model_outputs_status", "estimate_views_status")


def open_reader():
    """One pinned PostgreSQL reader from the explicit SHARED_DATA_* application settings."""
    import os
    return open_configured_reader(environ={**os.environ, "SHARED_DATA_BACKEND": "postgres"})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--schema-only", action="store_true", help="Show views/columns without reading business rows")
    parser.add_argument("--view", choices=sorted(VIEWS), default="store_day")
    parser.add_argument("--principal-id", help="Scoped local test identity; applications use a verified session")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--allow-candidate-estimates", action="store_true",
                        help="Explicitly read separate candidate estimates; not operational approval")
    args = parser.parse_args(argv)
    if not args.schema_only and not args.principal_id:
        parser.error("--principal-id is required for business-row reads")
    if not 1 <= args.limit <= 10000:
        parser.error("--limit must be between 1 and 10000")
    reader = None
    try:
        reader = open_reader()
        result = {key: reader.record.get(key) for key in IDENTITY}
        if args.schema_only:
            result["views"] = {name: [c["name"] + ":" + c["type"] for c in spec["columns"]]
                               for name, spec in reader.schema().items()}
        else:
            rows = reader.read(args.view, args.principal_id, start=args.start, end=args.end, limit=args.limit,
                               allow_candidate_estimates=args.allow_candidate_estimates)
            result.update(view=args.view, row_count=len(rows), rows=rows,
                          result_scope="Bounded rows, not a complete dataset or an aggregate API")
    except DataUnavailable as error:
        print(f"Data unavailable: {error}", file=sys.stderr)
        return 3
    except (ConfigError, PermissionError, ValueError, ConnectionError) as error:
        print(f"Read failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    finally:
        if reader is not None:
            reader.close()
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
