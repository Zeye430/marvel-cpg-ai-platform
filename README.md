# Marvel CPG AI Commercial Execution Platform

Course project for CDA650. The team will design and build an AI-powered commercial execution platform for the fictional company Marvel CPG.

## Project Scope

The final application will integrate four modules:

1. Commercial execution analytics dashboard
2. Seven-day out-of-stock risk prediction and store visit prioritization
3. Commercial negotiation and price scenario simulator
4. Conversational assistant with Text-to-SQL and retrieval-augmented generation

## Module 1: Commercial Execution Analytics Dashboard

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

## Shared V3 Data Platform

The four modules will consume a common Bronze/Silver/Gold analytical layer in DuckDB. The [V3 architecture and table catalog](docs/data/v3-medallion-architecture.md) define table grains, lineage, module dependencies, serving views and implementation stages. The [source contract](data_platform/contracts/v3/sources.json) records the eight V3 file schemas. The [KPI and missing-day specification](docs/solution-specifications/kpi-and-missing-store-day-handling.md) defines the shared business rules and validation requirements.

Stages 1-2 are implemented: versioned snapshots, Bronze ingestion, typed Silver, receipt reconciliation and the shared calendar. Run `uv run python -m data_platform build --publish-silver`; see the [pipeline runbook](data_platform/README.md) for artifacts, checks and time boundaries. Gold state/metrics, models and application serving follow in later stages. The Silver build is an internal artifact, not an application database.
