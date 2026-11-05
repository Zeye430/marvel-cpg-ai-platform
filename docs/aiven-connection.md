# Connect your module to the shared Aiven data

For Module 1, 2, 3 and 4 developers. Start here if you only need to **read the
already published business data**. The [data README](data/README.md) explains
the architecture; your [module guide](data/README.md) explains
which inputs to use.

## 1. What is ready, and what you own

The shared V3 serving tables have been published to Aiven PostgreSQL. All four
modules should consume this shared release rather than recreate raw-to-Gold ETL.

- The data maintainer handles raw files, DuckDB processing, validation and publication.
- You handle your module's backend data reads, field mapping, UI and business logic.
- You do **not** need your own Aiven account, raw CSVs, local DuckDB artifacts,
  a Docker database, another pipeline or a new upload.
- Publishing the database does not automatically switch existing module code.
  A legacy `DATABASE_URL` loader of `dim_*/fact_*` tables is not this interface.
- App integration/hosting is separate. You do not need to run the root `app.py`
  to test these reads. This guide uses the existing shared reader and example,
  not an unmerged module-specific adapter.

Current shared release:

| Check | Expected |
| --- | --- |
| Business data through | **2026-06-30**, treated as business “today” |
| Release ID | `r439c038733973c0547655802` |
| Source artifact | `20261022T011323-43fbaa38ede2` |
| Available views | 18; inspect the live schema below |
| Predictions | June 30 scoring for July 1–7; model remains a local-review candidate |
| Reconstructed data | Separate `estimated_*` candidate views, not approved observed facts |
| Scope | Demo assignments, not actual customer sales territories |

These identify the currently published snapshot, not a permanent release to
hard-code into your module. Ask the maintainer when the active release changes.
The application uses the current published release by default; each reader pins
one release for its lifetime.

## 2. Obtain the private connection pack

Ask the data maintainer for:

1. The **application read-only** PostgreSQL connection URL (host, port, database,
   username and password), with `sslmode=verify-full`.
2. The Aiven service CA certificate and instructions to save it locally.
3. Your assigned development principal and store/role scope.
4. The expected active release ID if it differs from the table above.

Do not use the database administrator/publisher login. Do not ask for or configure
`SHARED_DATA_PUBLISH_DATABASE_URL` to develop a module. Never put passwords or the
real connection URL in Git, screenshots, chat, browser JavaScript or LLM prompts.
Receive secrets through the team's agreed private channel.

The database credential identifies the backend service. The principal identifies
the user whose scope the backend reads. They are **not** interchangeable.
Development principals such as `demo:rep-a` are for trusted local checks only.
In an app, derive the principal from the authenticated server session, never a
browser parameter or generated SQL. The shared reader enforces user/store scope;
sharing the service credential with end users does not provide that protection.

## 3. Install the data-reading dependencies

From a checkout that contains `data_platform/postgres_reader.py` and
`examples/read_postgres_data.py`, use Python **3.13**:

```bash
uv sync --frozen --extra postgres
```

This installs data-reading dependencies, not every module's application/model
dependencies. If your module also needs Shiny or other extras, keep those extras
in your environment setup; see its guide. Do not run the pipeline to install data.

## 4. Configure your local environment

Use a private file **outside the repository**, for example
`~/.config/keystone/shared-data.env`. Create it in your editor with the actual
values received privately. The following is a shell-file template, not a working
credential:

```bash
export SHARED_DATA_BACKEND=postgres
export SHARED_DATA_DATABASE_URL='postgresql://APP_USER:URL_ENCODED_PASSWORD@DB_HOST:DB_PORT/DB_NAME?sslmode=verify-full'
export SHARED_DATA_SSLROOTCERT='/ABSOLUTE/PATH/TO/aiven-ca.pem'
```

Use the supplied complete URL rather than manually assembling it. Reserved
characters in passwords must be URL-encoded. The CA path must name a real local
file on **your** computer; another person's path will not work. Prefer a literal
absolute path. Generic dotenv loaders do not all expand shell `$HOME`.

Load the file into the same terminal that starts your module (macOS/Linux/WSL):

```bash
chmod 600 "$HOME/.config/keystone/shared-data.env"
source "$HOME/.config/keystone/shared-data.env"
unset SHARED_DATA_LOCAL_TEST
unset SHARED_DATA_RELEASE_ID
```

Do not weaken certificate verification or set local-test mode for Aiven.
`SHARED_DATA_RELEASE_ID` is optional: set it only when the maintainer explicitly
asks you to pin a particular **published** release. A private env file is not
automatically discovered by the shared reader; export/source it explicitly.
After changing exported settings, restart your app process. IDE run configurations
need the same environment; exports in one terminal do not affect another.

## 5. Check the connection and discover the tables

```bash
uv run python -m examples.read_postgres_data --schema-only
```

Expected: `backend: postgres`, the agreed `release_id`, `as_of: 2026-06-30`,
and 18 named views with columns/types. No local `business.duckdb` is required.

Then make a small scoped read, replacing `YOUR_ASSIGNED_PRINCIPAL` with the
principal supplied by the maintainer:

```bash
uv run python -m examples.read_postgres_data \
  --principal-id YOUR_ASSIGNED_PRINCIPAL \
  --view store_day --start 2026-06-30 --end 2026-06-30 --limit 5
```

This is a sample, **not** a complete portfolio KPI. Exit 0 means the command
succeeded; zero rows can mean the chosen date/scope has no matching rows.
An unassigned principal must be denied, not granted all stores.

For trusted demo checks, the currently configured manager scope contains 221
stores and rep-a contains 37. Do not hard-code those counts as production roles.
An assigned sales principal cannot read global model health/monitoring reports.

## 6. Read from your module's backend

This example is intentionally small. It works without the root app or a
module-specific adapter:

```python
from datetime import date

from data_platform.postgres_config import PostgresConfig
from data_platform.readers import open_business_reader


def load_store_day(verified_principal_id):
    # Call from trusted backend code after resolving the signed-in identity.
    reader = open_business_reader(
        backend="postgres",
        postgres_config=PostgresConfig.from_env("app"),
    )
    try:
        as_of = date.fromisoformat(reader.record["as_of"])
        rows = reader.read(
            "store_day",
            verified_principal_id,
            start=as_of,
            end=as_of,
            limit=1000,
        )
        return {
            "release_id": reader.record["release_id"],
            "as_of": reader.record["as_of"],
            "rows": rows,
            "result_scope": "bounded sample",
        }
    finally:
        reader.close()
```

Pass reader names such as `store_day`, **not** `gold.store_day` or raw table
names. SQL-facing views live under `platform_serving.vw_<name>`; the reader
handles the pinned release, scope checks, types and query parameters.

Reuse a reader for one coherent operation/session where appropriate and close
it on teardown. Do not reconnect for every table row. Creating a new reader picks
up the current publication; an existing reader does not switch halfway through
an operation.

### Reading more than a sample

`read(...)` returns at most 10,000 rows. Never treat the first 10,000 rows as all
stores/history or silently compute totals from a truncated sample.

`read_page(...)` supports these views: `stores`, `products`,
`store_sku_daily`, `oos_predictions`, `visit_priority`, `model_health`,
`model_monitoring`. Page size is at most 1,000; use returned cursors unchanged,
with the same reader/principal/filters:

```python
from datetime import date


def iter_daily_rows(reader, verified_principal_id):
    cursor = None
    while True:
        page = reader.read_page(
            "store_sku_daily",
            verified_principal_id,
            start=date(2026, 6, 1),
            end=date(2026, 6, 30),
            page_size=1000,
            after=cursor,
        )
        yield from page["rows"]
        cursor = page["next_cursor"]
        if cursor is None:
            break
```

Other views are **not** automatically pageable. For complete large aggregates,
agree a scoped aggregate/extraction interface with the data maintainer; do not
bypass scope or invent a new raw-data pipeline. Keep reads narrow by date/store/
SKU where supported. Do not upload entire business tables into an LLM prompt.
Full-history extraction can be slow even when small reads are fast.

## 7. Which data should your module use?

| Module | Start with | Meaning / next guide |
| --- | --- | --- |
| 1 | `store_day`, `store_sku_daily`, `commercial_period`, `execution_visits`, `stores`, `products` | Shared KPI components and reported facts; [Module 1 guide](data/v3-module-1-integration.md) |
| 2 UI | `oos_predictions`, `visit_priority`, `store_sku_daily`, `stores`, `products` | Published probabilities/rankings; global `model_health` / `model_monitoring` require manager/full scope; [Module 2 guide](data/v3-module-2-integration.md) |
| 3 | `negotiation_context`, `commercial_period`, `products`, `execution_visits` | Reference economics and evidence, not automatic approval; [Module 3 guide](data/v3-module-3-integration.md) |
| 4 | Reported views, `metric_definitions`, `delivery_events`, `sell_in_events`, `promotions` | Safe scoped query inputs; document retrieval is separate; [Module 4 guide](data/v3-module-4-integration.md) |

Use `reader.schema()` as the current column inventory and the
[Gold dictionary](data/v3-gold-data-dictionary.md) for field meanings. The source
[PostgreSQL contract](../data_platform/contracts/v3/postgres-serving.json) defines
the full current interface. Some dictionary sections describe older base
snapshots; inspect current release/model status rather than assuming prediction
tables are empty.

Shared rules you must retain:

- Missing reported stock/sales is **Unknown**, not zero.
- Estimated views require explicit `allow_candidate_estimates=True` on `read`;
  show Observed/Estimated/Unknown and Candidate status. Candidate is not approval.
- Sum KPI numerators/denominators before dividing. Do not average store percentages.
- Commercial period families and audit scopes overlap; select the appropriate
  family/scope before aggregation. Invoice units are not inventory receipts.
- Guardrails remain **Requires review**; the data does not supply approved
  trade-off verification, historical costs/currency or competitor shelf space.
- June 30 is the business cutoff. Model training/test data is separately
  controlled; this application connection does not unlock labels or authorize ML.

## 8. Read-only data is not application storage

The Aiven business tables are immutable shared input. Module developers must
not insert chat messages, negotiation drafts, approvals or UI preferences into
them, and must not request publisher permissions for that purpose.

If your module needs to save user activity, coordinate separate writable
application storage with its owner. Module 4 additionally needs its language
model, knowledge documents/index and conversation storage; a successful SQL
connection alone does not provide those services. You do not need those services
to test the small data-read examples above.

## 9. Troubleshooting

| Symptom | Check |
| --- | --- |
| `ConfigError`: URL missing | Source/export the private file in the terminal or IDE that starts the backend; use `SHARED_DATA_DATABASE_URL`, not only legacy `DATABASE_URL` |
| Missing CA / certificate error | Use your own existing absolute CA path and supplied host; keep `sslmode=verify-full` |
| Permission denied for principal | Confirm the assigned principal/scope; request configuration from the maintainer, not an admin login |
| `model_health` denied | It needs a manager with full-population scope; a sales principal is intentionally denied |
| Table/view missing | Use the reader name, inspect `--schema-only`, and compare release ID; raw `fact_*` tables are not this interface |
| No local serving.json | Your code is still choosing DuckDB; explicitly open the PostgreSQL reader / set the backend in code that uses that setting |
| Connection succeeds, module still loads CSVs | The module still uses its old loader; its developer must wire the shared reader into its backend |
| Empty sample / N/A / unavailable output | Inspect date, scope, coverage and output status; do not replace missing values with zeros |
| Slow full-history load | Narrow the query, use proper pagination or a reviewed scoped aggregate/cache; do not read every table on each UI interaction |
| Reader shows an older publication | Close/recreate it or restart the app; check an explicit `SHARED_DATA_RELEASE_ID` pin |
| Can't write business tables | Expected: this is the read-only application role; application writes need a separate store |

Stop and report the error **type**, release ID, chosen view and nonsecret settings.
Do not send the connection URL/password, private scope maps or business rows in
public issues. Do not run `init`, `load`, `activate`, `validate`,
`configure-scopes` or `rollback` to troubleshoot a module read: those are
maintainer operations, some of which mutate the release/access state.
