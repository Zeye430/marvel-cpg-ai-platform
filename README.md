# Marvel CPG AI Commercial Execution Platform

Course project for CDA650. The team will design and build an AI-powered commercial execution platform for the fictional company Marvel CPG.

## Project Scope

The final application will integrate four modules:

1. Commercial execution analytics dashboard
2. Seven-day out-of-stock risk prediction and store visit prioritization
3. Commercial negotiation and price scenario simulator
4. Conversational assistant with Text-to-SQL and retrieval-augmented generation

## Module 1: Commercial Execution Analytics Dashboard

> **V2 migration status:** Module 1 is retained for its assigned owner to migrate. The current implementation still references the archived V1 data layout and must be updated and revalidated before the Docker quick start is treated as V2-ready. Use the `v1-repository-final` tag for the complete runnable V1 state.

### Docker Quick Start Guide

Ensure Docker and Docker Compose are installed:

```bash
docker --version
docker compose version
```

Navigate to the Module 1 directory:

```bash
cd module_1
```

Build and start the container:

```bash
docker compose up --build
```

Open the dashboard in a browser:

```text
http://localhost:8000
```

Stop the container:

```bash
docker compose down
```

## Development Environment

The project uses Python 3.13 and `uv` for Python and dependency management.

After cloning the repository and installing [`uv`](https://docs.astral.sh/uv/getting-started/installation/), run:

```bash
uv sync
uv run jupyter lab
```

Run Python commands through `uv run` so they use the project environment. Commit `pyproject.toml`, `uv.lock`, and `.python-version`, but do not commit `.venv/`.

## Active Data Version

Active development uses the updated eight-table client dataset, identified as **v2**. Authorized team members should obtain the files through the team's approved private file-sharing location and place them in `data/raw/`.

The raw files are intentionally excluded from Git. See `data/README.md` and `data/manifests/v2.md` for the required files and version checks. New data-dependent analysis belongs under `notebooks/v2/`.

The original dataset and its analyses are preserved on the `archive/v1-old-data` branch. Use the `v1-repository-final` tag for the complete historical V1 repository state.

## Team Workflow

- Keep `main` stable and do not develop directly on it.
- Create a branch for each assigned task.
- Open a pull request when the work is ready for review.
- Ask at least one teammate to review the pull request before merging.
- Do not commit API keys, passwords, local environment files, or other secrets.

## Data and Confidentiality

The supplied Marvel CPG data and challenge brief are for academic use only. Do not add the raw client files to this repository unless the instructor explicitly approves it. Document how authorized team members can obtain and place the data locally.

## Status

The initial Module 1 implementation and project environment configuration are available. The remaining modules are under development.
