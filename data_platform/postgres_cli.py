"""Operator commands for the shared PostgreSQL serving backend.

    python -m data_platform.postgres_cli inspect-source --artifact-dir <serving build>
    python -m data_platform.postgres_cli preflight
    python -m data_platform.postgres_cli init [--apply --confirm-database <db>]
    python -m data_platform.postgres_cli load --artifact-dir <dir> [--apply --confirm-database <db>]
    python -m data_platform.postgres_cli validate --release-id <id>
    python -m data_platform.postgres_cli activate --release-id <id> [--expect-current <publication>] [--apply --confirm-database <db>]
    python -m data_platform.postgres_cli rollback --publication-id <id> [--apply --confirm-database <db>]
    python -m data_platform.postgres_cli configure-scopes --policy-file <json> [--apply --confirm-database <db>]

Connection settings come only from SHARED_DATA_PUBLISH_DATABASE_URL and
SHARED_DATA_SSLROOTCERT (never from arguments). inspect-source and preflight are
read-only. init, load, activate, rollback and configure-scopes are dry runs unless
given --apply --confirm-database. validate has no dry run: it is an explicit
operator operation that records its result, and on failure marks the release
Failed and withdraws reader access to it (exit status 1). Output is one JSON
object without data rows or secrets.
"""

import argparse
import json
import sys

MUTATING = ("init", "load", "activate", "rollback", "configure-scopes")


def parser():
    p = argparse.ArgumentParser(prog="python -m data_platform.postgres_cli",
                                description="Publish verified serving data to PostgreSQL. init/load/activate/rollback/"
                                            "configure-scopes are dry runs unless --apply; validate records its "
                                            "result and fails closed; inspect-source/preflight are read-only")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect-source", help="Verify a local serving artifact (no database)") \
        .add_argument("--artifact-dir", required=True)
    commands.add_parser("preflight", help="Read-only target diagnostics and size report")
    commands.add_parser("init", help="Create the platform namespaces once")
    commands.add_parser("load", help="Import an isolated candidate release").add_argument("--artifact-dir", required=True)
    commands.add_parser("validate", help="Verify a release payload and record the result (no dry run: a failure "
                                          "marks it Failed and withdraws reader access)") \
        .add_argument("--release-id", required=True)
    activate = commands.add_parser("activate", help="Publish a validated release atomically")
    activate.add_argument("--release-id", required=True)
    activate.add_argument("--expect-current", help="Refuse unless this publication is still current")
    commands.add_parser("rollback", help="Re-publish the release of an earlier publication") \
        .add_argument("--publication-id", required=True)
    commands.add_parser("configure-scopes", help="Replace store-scope assignments") \
        .add_argument("--policy-file", required=True)
    for name in MUTATING:
        sub = commands.choices[name]
        sub.add_argument("--apply", action="store_true", help="Perform the change (default: dry run)")
        sub.add_argument("--confirm-database", help="Required with --apply; must equal the target database name")
    return p


def run(argv=None, environ=None):
    args = parser().parse_args(argv)
    from . import postgres_publisher as publisher
    from .postgres_config import ConfigError, PostgresConfig, sanitize
    try:
        if args.command == "inspect-source":
            return 0, publisher._public(publisher.inspect_serving_source(args.artifact_dir))
        config = PostgresConfig.from_env("publisher", environ)
        apply = bool(getattr(args, "apply", False))
        if apply and args.confirm_database != config.dbname:
            raise ConfigError("--apply requires --confirm-database matching the target database name")
        if args.command == "preflight":
            result = publisher.preflight_postgres(config)
        elif args.command == "init":
            result = publisher.initialize_postgres(config, apply=apply)
        elif args.command == "load":
            result = publisher.load_postgres_candidate(args.artifact_dir, config, apply=apply)
        elif args.command == "validate":
            result = publisher.validate_postgres_release(config, args.release_id)
        elif args.command == "activate":
            result = publisher.activate_postgres_release(config, args.release_id, apply=apply,
                                                         expected_current_publication_id=args.expect_current)
        elif args.command == "rollback":
            result = publisher.rollback_postgres_release(config, args.publication_id, apply=apply)
        else:
            result = publisher.configure_postgres_scopes(config, args.policy_file, apply=apply)
        return 0, result
    except (ConfigError, publisher.PublishError, ConnectionError, ValueError, OSError) as error:
        message = str(error) if not hasattr(error, "pgcode") else sanitize(error)
        return 1, {"command": args.command, "status": "Failed", "error": f"{type(error).__name__}: {message}"}
    except Exception as error:                       # driver errors: class/code/first line only
        return 1, {"command": args.command, "status": "Failed", "error": sanitize(error)}


def main(argv=None):
    code, result = run(argv)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
