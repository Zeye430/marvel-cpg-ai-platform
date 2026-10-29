"""
modules/module_4/sql_guard.py
=============================
Checks model-written SQL before it runs (Module 4 guide, section 4).

DuckDB's own parser (json_serialize_sql) is the source of truth, so the check sees
the query exactly as DuckDB would run it. A query passes only if it is:

1. exactly one SELECT statement (ATTACH, COPY, SET, PRAGMA, INSERT, ... cannot be
   serialized and are rejected; so is a second statement hidden after a comment),
2. reading only the sandbox's allowlisted tables or its own CTEs, with no schema or
   catalog prefix (no information_schema, duckdb_* or attached databases),
3. free of table functions (read_csv, query, glob, ...) and denied scalar functions.

This is one layer. The sandbox itself (sandbox.py) holds only the signed-in user's
stores, has file/network access switched off and caps time and rows.
"""

from __future__ import annotations

import json
import re

import duckdb

DENIED_FUNCTIONS = frozenset({
    "getenv", "current_setting", "query", "query_table", "json_execute_serialized_sql",
    "sql_auto_complete", "read_text", "read_blob", "glob", "sniff_csv", "load", "install",
})
DENIED_PREFIXES = ("read_", "duckdb_", "pragma_", "parquet_", "iceberg_", "delta_", "postgres_", "sqlite_",
                   "mysql_", "pg_", "http", "s3")
MAX_SQL_CHARS = 6000


class UnsafeSQL(ValueError):
    """The SQL was rejected before execution; the message is safe to show."""


_parser = duckdb.connect(":memory:")


def _walk(node, found):
    if isinstance(node, dict):
        kind = node.get("type")
        if kind == "BASE_TABLE":
            found["tables"].append((node.get("catalog_name") or "", node.get("schema_name") or "",
                                    node.get("table_name") or ""))
        elif kind == "TABLE_FUNCTION":
            found["table_functions"] += 1
        if kind == "FUNCTION" or "function_name" in node:
            name = node.get("function_name")
            if name:
                found["functions"].add(name.lower())
        cte_map = node.get("cte_map")
        if isinstance(cte_map, dict):
            for entry in cte_map.get("map") or []:
                if isinstance(entry, dict) and entry.get("key"):
                    found["ctes"].add(entry["key"].lower())
        for value in node.values():
            _walk(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk(value, found)


def extract_sql(text: str) -> str:
    """Pull the SQL out of a model reply (fenced block or bare statement)."""
    fenced = re.findall(r"```(?:sql|duckdb)?\s*(.*?)```", text, flags=re.S | re.I)
    sql = fenced[0] if fenced else text
    sql = sql.strip().rstrip(";").strip()
    return sql


def check_sql(sql: str, allowed_tables) -> str:
    """Return the cleaned SQL if it passes every rule, else raise UnsafeSQL."""
    allowed = {t.lower() for t in allowed_tables}
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise UnsafeSQL("No SQL was produced.")
    if len(sql) > MAX_SQL_CHARS:
        raise UnsafeSQL("The SQL is too long.")
    try:
        parsed = json.loads(_parser.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])
    except duckdb.Error as error:
        raise UnsafeSQL(f"The SQL could not be parsed ({type(error).__name__}).") from error
    if parsed.get("error"):
        message = str(parsed.get("error_message", ""))
        if "Only SELECT" in message:
            raise UnsafeSQL("Only a single read-only SELECT query is allowed.")
        raise UnsafeSQL("The SQL has a syntax error: " + message[:200])
    statements = parsed.get("statements") or []
    if len(statements) != 1:
        raise UnsafeSQL("Exactly one SELECT statement is allowed.")
    found = {"tables": [], "table_functions": 0, "functions": set(), "ctes": set()}
    _walk(statements[0], found)
    if found["table_functions"]:
        raise UnsafeSQL("Table functions (file readers, query(), ...) are not allowed.")
    for catalog, schema, table in found["tables"]:
        if catalog or schema:
            raise UnsafeSQL(f"Schema-qualified names are not allowed ({schema or catalog}.{table}).")
        if table.lower() not in allowed and table.lower() not in found["ctes"]:
            raise UnsafeSQL(f"Unknown table '{table}'. Allowed tables: {', '.join(sorted(allowed))}.")
    if not found["tables"]:
        raise UnsafeSQL("The query must read from one of the copilot's tables.")
    for name in found["functions"]:
        if name in DENIED_FUNCTIONS or name.startswith(DENIED_PREFIXES):
            raise UnsafeSQL(f"Function '{name}' is not allowed.")
    return sql
