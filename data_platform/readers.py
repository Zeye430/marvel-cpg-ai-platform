"""Backend-neutral business reader interface and explicit factory.

DuckDB (a local verified serving artifact) is the default. PostgreSQL is an
explicit opt-in that reads a published shared release with the same API,
limits, scope rules and error types. Importing this module never connects to a
database or imports the optional PostgreSQL driver.
"""

from typing import Protocol, runtime_checkable

from .postgres_config import BACKENDS, ConfigError, PostgresConfig, RELEASE_ENV, configured_backend

__all__ = ["BusinessReader", "open_business_reader", "open_configured_reader"]


@runtime_checkable
class BusinessReader(Protocol):
    record: dict

    def schema(self): ...

    def read(self, name, principal_id, *, start=None, end=None, limit=1000, allow_candidate_estimates=False): ...

    def read_page(self, name, principal_id, *, start=None, end=None, store_id=None, sku_id=None, after=None,
                  page_size=1000): ...

    def principal_scope(self, principal_id): ...


def open_business_reader(*, backend="duckdb", artifact_dir=None, postgres_config=None, release_id=None):
    """Open one pinned reader. ``backend`` is chosen explicitly; there is no silent fallback."""
    if backend not in BACKENDS:
        raise ConfigError(f"backend must be one of {', '.join(BACKENDS)}")
    if backend == "duckdb":
        if artifact_dir is None or postgres_config is not None or release_id is not None:
            raise ConfigError("The DuckDB backend needs artifact_dir only")
        from .access import ServingReader
        return ServingReader(artifact_dir)
    if artifact_dir is not None or not isinstance(postgres_config, PostgresConfig):
        raise ConfigError("The PostgreSQL backend needs a PostgresConfig and no artifact_dir")
    from .postgres_reader import PostgresServingReader
    return PostgresServingReader(postgres_config, release_id=release_id)


def open_configured_reader(*, artifact_dir=None, environ=None):
    """Reader from SHARED_DATA_BACKEND (duckdb default) and the app's explicit PostgreSQL settings."""
    import os
    env = os.environ if environ is None else environ
    backend = configured_backend(env)
    if backend == "duckdb":
        if artifact_dir is None:
            raise ConfigError("The DuckDB backend needs a serving artifact directory")
        return open_business_reader(artifact_dir=artifact_dir)
    return open_business_reader(backend="postgres", postgres_config=PostgresConfig.from_env("app", env),
                                release_id=env.get(RELEASE_ENV) or None)
