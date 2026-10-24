"""Validated PostgreSQL connection settings for the shared serving backend.

Settings come only from explicit environment names (never a legacy
DATABASE_URL, never a .env file loaded here, never a password on a command
line). Remote targets must verify the server certificate (sslmode=verify-full
with a CA file). A plain or relaxed-TLS connection is allowed only in explicit
local-test mode against a numeric loopback address or a Unix socket.
Importing this module never connects or imports a database driver.
"""

import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

APP_ENV = "SHARED_DATA_DATABASE_URL"
PUBLISH_ENV = "SHARED_DATA_PUBLISH_DATABASE_URL"
SSLROOTCERT_ENV = "SHARED_DATA_SSLROOTCERT"
RELEASE_ENV = "SHARED_DATA_RELEASE_ID"
BACKEND_ENV = "SHARED_DATA_BACKEND"
LOCAL_TEST_ENV = "SHARED_DATA_LOCAL_TEST"
PUBLISH_TIMEOUT_ENV = "SHARED_DATA_PUBLISH_STATEMENT_TIMEOUT_SECONDS"
BACKENDS = ("duckdb", "postgres")
PURPOSES = {"app": APP_ENV, "publisher": PUBLISH_ENV}
_ALLOWED_PARAMS = {"host", "sslmode", "sslrootcert", "application_name", "connect_timeout"}
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]{0,62}")


class ConfigError(ValueError):
    """Invalid or unsafe connection configuration; messages never contain secrets."""


def _bounded(value, name, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be an integer") from None
    if isinstance(value, bool) or not low <= number <= high:
        raise ConfigError(f"{name} must be between {low} and {high}")
    return number


def _local_host(host):
    if host.startswith("/"):
        return True                                       # Unix socket directory
    try:
        return ipaddress.ip_address(host).is_loopback      # numeric loopback only
    except ValueError:
        return False


@dataclass(frozen=True)
class PostgresConfig:
    """One validated target. ``repr`` and ``describe`` never include the password."""

    host: str
    port: int
    dbname: str
    user: str
    password: str = field(repr=False, default="")
    sslmode: str = "verify-full"
    sslrootcert: str = None
    purpose: str = "app"
    local_test: bool = False
    connect_timeout: int = 5
    statement_timeout_ms: int = 5000
    max_connections: int = 2
    application_name: str = "shared-serving"

    @classmethod
    def from_url(cls, url, *, purpose="app", sslrootcert=None, local_test=False, statement_timeout_ms=None,
                 max_connections=2, application_name=None):
        if purpose not in PURPOSES and purpose != "test":
            raise ConfigError("purpose must be app, publisher or test")
        if not isinstance(url, str) or not url.strip():
            raise ConfigError("A PostgreSQL URL is required")
        parts = urlsplit(url.strip())
        if parts.scheme not in ("postgresql", "postgres"):
            raise ConfigError("Only postgresql:// URLs are supported")
        try:
            port = parts.port or 5432
        except ValueError:
            raise ConfigError("Invalid port") from None
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        unknown = set(params) - _ALLOWED_PARAMS
        if unknown:
            raise ConfigError(f"Unsupported connection parameters: {sorted(unknown)}")
        host = unquote(params.pop("host", "") or (parts.hostname or ""))
        dbname = unquote(parts.path.lstrip("/"))
        user = unquote(parts.username or "")
        if not host or not dbname or not user:
            raise ConfigError("URL needs host, database and user")
        if not _IDENTIFIER.fullmatch(dbname):
            raise ConfigError("Database name must be a plain identifier")
        sslmode = params.get("sslmode") or ("disable" if local_test else "verify-full")
        cert = sslrootcert or params.get("sslrootcert")
        if local_test is not True and local_test is not False:
            raise ConfigError("local_test must be a real boolean")
        if local_test:
            if not _local_host(host):
                raise ConfigError("Local-test mode requires a numeric loopback address or Unix socket")
            if sslmode not in ("disable", "prefer", "require", "verify-ca", "verify-full"):
                raise ConfigError("Unsupported sslmode")
        else:
            if sslmode != "verify-full":
                raise ConfigError("Remote connections require sslmode=verify-full")
            if not cert or not Path(cert).is_file():
                raise ConfigError(f"Remote connections require a CA file ({SSLROOTCERT_ENV})")
        if statement_timeout_ms is None:
            statement_timeout_ms = 5000 if purpose == "app" else 3_600_000
        return cls(host=host, port=port, dbname=dbname, user=user, password=unquote(parts.password or ""),
                   sslmode=sslmode, sslrootcert=cert, purpose=purpose, local_test=local_test,
                   connect_timeout=_bounded(params.get("connect_timeout", 5), "connect_timeout", 1, 60),
                   statement_timeout_ms=_bounded(statement_timeout_ms, "statement timeout (ms)", 100, 86_400_000),
                   max_connections=_bounded(max_connections, "max_connections", 1, 8),
                   application_name=application_name or params.get("application_name") or f"shared-serving-{purpose}")

    @classmethod
    def from_env(cls, purpose="app", environ=None):
        """Read only the explicit shared-data variables for ``purpose`` (app or publisher)."""
        env = os.environ if environ is None else environ
        if purpose not in PURPOSES:
            raise ConfigError("purpose must be app or publisher")
        url = env.get(PURPOSES[purpose])
        if not url:
            raise ConfigError(f"{PURPOSES[purpose]} is not set")
        local = env.get(LOCAL_TEST_ENV, "") == "1"
        timeout = None
        if purpose == "publisher" and env.get(PUBLISH_TIMEOUT_ENV):
            timeout = _bounded(env[PUBLISH_TIMEOUT_ENV], PUBLISH_TIMEOUT_ENV, 1, 86_400) * 1000
        return cls.from_url(url, purpose=purpose, sslrootcert=env.get(SSLROOTCERT_ENV) or None, local_test=local,
                            statement_timeout_ms=timeout)

    def describe(self):
        """Redacted, loggable description of the target."""
        return {"host": self.host, "port": self.port, "dbname": self.dbname, "user": self.user,
                "sslmode": self.sslmode, "ca_file_configured": bool(self.sslrootcert), "purpose": self.purpose,
                "local_test": self.local_test, "statement_timeout_ms": self.statement_timeout_ms,
                "max_connections": self.max_connections}

    def connect_kwargs(self):
        kwargs = {"host": self.host, "port": self.port, "dbname": self.dbname, "user": self.user,
                  "sslmode": self.sslmode, "connect_timeout": self.connect_timeout,
                  "application_name": self.application_name,
                  "options": f"-c statement_timeout={self.statement_timeout_ms} -c idle_in_transaction_session_timeout=60000"}
        if self.password:
            kwargs["password"] = self.password
        if self.sslrootcert:
            kwargs["sslrootcert"] = self.sslrootcert
        return kwargs

    def connect(self):
        import psycopg2                                   # lazy: optional dependency
        try:
            return psycopg2.connect(**self.connect_kwargs())
        except psycopg2.Error as error:
            raise ConnectionError(sanitize(error, self)) from None


def sanitize(error, config=None):
    """Short error text without passwords, URLs or server detail blocks."""
    text = str(getattr(error, "pgerror", None) or error).strip().splitlines()
    first = text[0] if text else ""
    if config is not None and config.password:
        first = first.replace(config.password, "***")
    first = re.sub(r"postgres(?:ql)?://\S+", "postgresql://***", first)
    first = re.sub(r"password=\S+", "password=***", first)
    code = getattr(error, "pgcode", None)
    return f"{type(error).__name__}{f' [{code}]' if code else ''}: {first[:300]}"


def configured_backend(environ=None):
    env = os.environ if environ is None else environ
    backend = env.get(BACKEND_ENV, "duckdb") or "duckdb"
    if backend not in BACKENDS:
        raise ConfigError(f"{BACKEND_ENV} must be one of {', '.join(BACKENDS)}")
    return backend
