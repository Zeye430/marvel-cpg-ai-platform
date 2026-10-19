"""Persist quality evidence without deleting or repairing source records."""

import json
from dataclasses import asdict, dataclass, field


class QualityError(RuntimeError):
    """A candidate failed a required publication gate."""


@dataclass
class Check:
    check_id: str
    scope_id: str
    severity: str
    failed_count: int
    checked_count: int | None = None
    tolerance: float | None = None
    evidence: list = field(default_factory=list)
    note: str = ""

    @property
    def passed(self):
        return self.failed_count == 0


class Quality:
    def __init__(self, connection, build_id):
        self.connection = connection
        self.build_id = build_id
        self.checks = []

    def record(self, check_id, scope_id, failed_count, *, checked_count=None,
               severity="error", tolerance=None, evidence=None, note=""):
        check = Check(check_id, scope_id, severity, int(failed_count), checked_count,
                      tolerance, evidence or [], note)
        self.checks.append(check)
        self.connection.execute(
            "INSERT INTO meta.quality_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [self.build_id, check_id, scope_id, severity, check.passed,
             check.failed_count, checked_count, tolerance,
             json.dumps(check.evidence, default=str), note],
        )
        return check

    def query(self, check_id, scope_id, bad_rows_sql, **kwargs):
        if "checked_count" not in kwargs:
            # Source checks use Bronze until typed Silver exists; business checks
            # then use the authorized Silver population. No outcome statistics.
            relation = self.connection.execute("""
                SELECT table_schema, table_name FROM information_schema.tables
                WHERE table_name = ? AND table_schema IN ('bronze', 'silver')
                ORDER BY CASE table_schema WHEN 'silver' THEN 0 ELSE 1 END LIMIT 1
            """, [scope_id]).fetchone()
            if relation:
                schema, table = (value.replace('"', '""') for value in relation)
                kwargs["checked_count"] = self.connection.execute(
                    f'SELECT count(*) FROM "{schema}"."{table}"'
                ).fetchone()[0]
        count = self.connection.execute(
            f"SELECT count(*) FROM ({bad_rows_sql}) AS failures"
        ).fetchone()[0]
        evidence = []
        if count:
            cursor = self.connection.execute(f"SELECT * FROM ({bad_rows_sql}) AS failures LIMIT 5")
            names = [column[0] for column in cursor.description]
            evidence = [dict(zip(names, row)) for row in cursor.fetchall()]
        return self.record(check_id, scope_id, count, evidence=evidence, **kwargs)

    def require_pass(self):
        failures = [c for c in self.checks if not c.passed and c.severity == "error"]
        if failures:
            names = ", ".join(f"{c.scope_id}/{c.check_id}: {c.failed_count}" for c in failures)
            raise QualityError(f"Required quality checks failed: {names}")

    def report(self):
        return [{**asdict(check), "passed": check.passed} for check in self.checks]
