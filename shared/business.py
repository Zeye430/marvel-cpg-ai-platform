"""Open a pinned business reader, never rebuild raw data or fall back on failure."""

import os
from pathlib import Path


def open_reader(environ=None):
    env = os.environ if environ is None else environ
    backend = env.get("SHARED_DATA_BACKEND", "duckdb")
    if backend == "postgres":
        from data_platform.readers import open_configured_reader
        return open_configured_reader(environ=env)
    if backend != "duckdb":
        raise ValueError("Unsupported shared data backend")
    from examples.read_shared_data import open_reader as local_reader
    artifact = env.get("SHARED_DATA_ARTIFACT_DIR")
    publication = env.get("SHARED_DATA_PUBLICATION")
    if artifact and publication:
        raise ValueError("Choose a shared artifact directory or publication, not both")
    return local_reader(artifact_dir=Path(artifact) if artifact else None,
                        publication=Path(publication) if publication else None)
