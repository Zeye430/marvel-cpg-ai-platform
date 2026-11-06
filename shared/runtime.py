"""One pinned shared business source for the integrated app.

The app shell opens a single verified reader (shared.business.open_reader: local
DuckDB publication or the opt-in shared PostgreSQL release) lazily when the first
browser session needs it, never at import time and never from raw CSVs or legacy
tables. A failed open is retried after ``retry_seconds`` by later sessions.
Every module in the shell reads that same source identity. A failure is an
explicit unavailable state with a sanitized reason; there is no fallback to
another backend, artifact or local copy.

A module-specific source setting that names a different publication is a split
configuration: the affected module is shown unavailable instead of silently
displaying results from another source next to the shared one.
"""

import os
import threading
import time
from pathlib import Path

IDENTITY_FIELDS = ("backend", "artifact_id", "release_id", "source_artifact_id", "dataset_hash", "as_of",
                   "database_sha256", "model_outputs_status")


def source_key(record):
    """What makes two readers the same business data (one artifact, byte-identical database)."""
    artifact = record.get("source_artifact_id") or record.get("artifact_id")
    return artifact, record.get("database_sha256"), record.get("dataset_hash"), str(record.get("as_of"))


def identity(record):
    data = {k: record.get(k) for k in IDENTITY_FIELDS if record.get(k) is not None}
    data["backend"] = record.get("backend", "duckdb")
    outputs = record.get("model_outputs") or {}
    for key in ("publication_kind", "model_version", "scoring_run_id", "scoring_date", "synthetic"):
        if outputs.get(key) is not None:
            data["model_" + key] = outputs[key]
    return data


class SourceUnavailable(RuntimeError):
    """The configured shared source cannot be opened (sanitized message)."""


class SharedRuntime:
    def __init__(self, environ=None, *, opener=None, retry_seconds=30.0):
        self.environ = dict(os.environ if environ is None else environ)
        self._opener = opener
        self._retry = retry_seconds
        self._lock = threading.Lock()
        self._reader = None
        self._failure = None  # (monotonic time, reason)
        self._services = {}

    # ------------------------------------------------------------------ source
    def reader(self):
        with self._lock:
            if self._reader is not None:
                return self._reader
            if self._failure and time.monotonic() - self._failure[0] < self._retry:
                raise SourceUnavailable(self._failure[1])
            try:
                if self._opener is not None:
                    reader = self._opener(self.environ)
                else:
                    from .business import open_reader
                    reader = open_reader(self.environ)
            except Exception as error:  # class name only: never URLs, paths or credentials
                backend = self.environ.get("SHARED_DATA_BACKEND", "duckdb")
                reason = f"Shared business data unavailable ({backend}: {type(error).__name__})"
                self._failure = (time.monotonic(), reason)
                raise SourceUnavailable(reason) from None
            self._reader, self._failure = reader, None
            return reader

    def status(self):
        try:
            reader = self.reader()
        except SourceUnavailable as error:
            return {"ready": False, "reason": str(error), "identity": {}}
        return {"ready": True, "reason": "", "identity": identity(reader.record)}

    def _service(self, name, build):
        """Cache a module service; one built while the source was down is rebuilt once it can recover."""
        with self._lock:
            entry = self._services.get(name)
            if entry is not None:
                value, source_failed = entry
                failure_active = self._reader is None and self._failure is not None and \
                    time.monotonic() - self._failure[0] < self._retry
                if not source_failed or failure_active:
                    return value
                del self._services[name]
        value, source_failed = build()
        with self._lock:
            return self._services.setdefault(name, (value, source_failed))[0]

    # ------------------------------------------------------------------ split checks
    def _split_reason(self, label, local_record):
        if source_key(local_record) != source_key(self.reader().record):
            return (f"{label} is configured with a different publication than the shared app source; "
                    "the integrated app shows one source only. Remove the module-specific setting or point it "
                    "at the same publication.")
        return None

    def _module_source_check(self, label, artifact_env=None, publication_env=None, root_env=None):
        """Validate an optional module-specific local setting against the shared source."""
        from data_platform.artifacts import verified_artifact
        artifact = self.environ.get(artifact_env) if artifact_env else None
        publication = self.environ.get(publication_env) if publication_env else None
        root = self.environ.get(root_env) if root_env else None
        if not (artifact or publication or root):
            return None
        try:
            if root:
                from modules.module_2.service import resolve_published
                artifact = resolve_published(Path(root))
            elif publication:
                import json
                artifact = json.loads(Path(publication).read_text())["directory"]
            record, _, _ = verified_artifact(artifact, kind="serving")
        except Exception as error:
            return f"{label} source setting could not be verified ({type(error).__name__})."
        return self._split_reason(label, record)

    # ------------------------------------------------------------------ module services
    def module1_pool(self):
        """Module 1's per-principal scoped caches on the shared reader (raises SourceUnavailable)."""
        from modules.module_1.data import Module1Pool
        return self._service("module1", lambda: (Module1Pool(self.reader()), False))

    def module2_service(self):
        """(service, reason): the standalone Module 2 service bound to the shared reader."""
        def build():
            try:
                reader = self.reader()
            except SourceUnavailable as error:
                return (None, str(error)), True
            try:
                reason = self._module_source_check("Module 2", root_env="MODULE2_SERVING_ROOT")
                if reason:
                    return (None, reason), False
                from datetime import date
                from modules.module_2.service import Module2Service
                latest = self.environ.get("MODULE2_LATEST_SOURCE_DATE")
                return (Module2Service(reader=reader,
                                       latest_source_as_of=date.fromisoformat(latest) if latest else None), None), False
            except Exception as error:
                return (None, f"Module 2 unavailable ({type(error).__name__})"), False
        return self._service("module2", build)

    def module2_business_ranking(self, principal_id):
        """Module 2's computed business-priority ranking for one principal, read through the same
        Module 2 service (same reader, scope check and policy version) the Module 2 pages use.

        Returns every authorized store with its business rank/score and the scope fingerprint the
        service verified; Module 4 copies it only if that matches its own sandbox. Raises when the
        service cannot answer. Nothing is recomputed here.
        """
        from data_platform.access import DataUnavailable
        from modules.module_2.service import Principal
        service, reason = self.module2_service()
        if service is None:
            raise DataUnavailable(reason)
        principal, rows, cursor, first = Principal(principal_id, True), [], None, None
        while True:
            response = service.get_priorities(principal, {"view": "all"}, cursor=cursor, page_size=200,
                                              sort=("rank", "asc"))
            if response.status not in ("Ok", "Stale", "Empty"):
                raise DataUnavailable(f"Module 2 ranking {response.status}")
            first = first or response
            if response.scope_identity != first.scope_identity:
                raise DataUnavailable("Module 2 ranking scope changed between pages")
            rows += response.rows
            cursor = response.next_cursor
            if cursor is None:
                break
        policy = first.totals["policy"]
        return {"scope_fingerprint": first.scope_identity, "scoring_run_id": first.scoring_run_id,
                "model_version": first.model_version, "policy_version": policy["version"],
                "policy_status": policy["status"],
                "rows": [{"store_id": r["store_id"], "store_name": r["store_name"], "channel": r["channel"],
                          "region": r["region"], "business_rank": r["overall_rank"],
                          "priority_score": r["priority_score"], "urgency": r["urgency_label"],
                          "sales_at_risk_usd": r["sales_at_risk_usd"],
                          "sales_at_risk_status": r["sales_at_risk_status"],
                          "risk_category": r["risk_category"],
                          "score_missing": ", ".join(r["score_missing_components"]) or None,
                          "stored_rank_in_scope": r["display_rank"],
                          "scoring_run_id": first.scoring_run_id if r["display_rank"] is not None else None,
                          "scoring_date": r["scoring_date"] if r["display_rank"] is not None else None,
                          "ranking_policy_version": policy["version"], "policy_status": policy["status"]}
                         for r in rows]}

    def copilot(self):
        """Module 4 copilot bound to the shared reader, or one carrying an explicit data reason."""
        def build():
            from modules.module_4.agent import Copilot
            try:
                reader = self.reader()
            except SourceUnavailable as error:
                return Copilot(reader=None, data_reason=str(error)), True
            reason = self._module_source_check("Module 4", artifact_env="MODULE4_SERVING_ARTIFACT_DIR",
                                               publication_env="MODULE4_SERVING_PUBLICATION")
            if reason:
                return Copilot(reader=None, data_reason=reason), False
            return Copilot(reader=reader, business_ranking=self.module2_business_ranking), False
        return self._service("module4", build)
