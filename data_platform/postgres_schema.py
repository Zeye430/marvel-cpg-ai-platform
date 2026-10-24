"""Declarative serving-view contract, DuckDB -> PostgreSQL types and lossless codecs.

The contract (contracts/v3/postgres-serving.json) lists every approved serving
view with its exact column order and source type. Nothing outside it is
exported. Values travel through COPY text format and are compared with a
canonical, typed row encoding so that NULL, empty text, the literal text
``\\N``/``null``, Unicode, newlines, decimals, -0.0, NaN and infinities are
all distinguishable and must round-trip exactly.
"""

import hashlib
import json
import math
import re
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from .serving import VIEWS

CONTRACT_PATH = Path(__file__).resolve().parent / "contracts" / "v3" / "postgres-serving.json"
INTERFACE_VERSION = "shared_postgres_serving.v1"
_DECIMAL = re.compile(r"DECIMAL\((\d{1,2}),(\d{1,2})\)")
_SIMPLE = {"VARCHAR": 'TEXT COLLATE "C"', "BOOLEAN": "BOOLEAN", "DATE": "DATE", "INTEGER": "INTEGER",
           "BIGINT": "BIGINT", "HUGEINT": "NUMERIC(39,0)", "DOUBLE": "DOUBLE PRECISION"}
_IDENT = re.compile(r"[a-z_][a-z0-9_]{0,62}")


class UnsupportedValue(ValueError):
    pass


@lru_cache(maxsize=1)
def _contract_bytes():
    return CONTRACT_PATH.read_bytes()


def load_contract():
    contract = json.loads(_contract_bytes())
    if contract.get("interface_version") != INTERFACE_VERSION:
        raise ValueError("Unsupported PostgreSQL serving contract")
    if set(contract["views"]) != set(VIEWS):
        raise ValueError("PostgreSQL serving contract differs from the serving view allowlist")
    for name, spec in contract["views"].items():
        relation, date_column, scoped = VIEWS[name]
        if (spec["relation"], spec["date_column"], spec["store_scoped"]) != (relation, date_column, scoped):
            raise ValueError(f"Contract entry for {name} differs from serving.VIEWS")
        names = [c for c, _ in spec["columns"]]
        if len(set(names)) != len(names) or not all(_IDENT.fullmatch(c) for c in names + [spec["relation"]]):
            raise ValueError(f"Contract columns for {name} are not plain unique identifiers")
        if not set(spec["key"]) <= set(names):
            raise ValueError(f"Contract key for {name} names unknown columns")
        if any(dict(spec["columns"])[k] == "DOUBLE" for k in spec["key"]):
            raise ValueError(f"Contract key for {name} must not contain floating-point columns")
        for _, logical in spec["columns"]:
            pg_type(logical)
    return contract


def contract_sha256():
    return hashlib.sha256(_contract_bytes()).hexdigest()


def pg_type(logical):
    if logical in _SIMPLE:
        return _SIMPLE[logical]
    match = _DECIMAL.fullmatch(logical)
    if match and 0 <= int(match.group(2)) <= int(match.group(1)) <= 38:
        return f"NUMERIC({match.group(1)},{match.group(2)})"
    raise ValueError(f"Unsupported column type for lossless PostgreSQL mapping: {logical}")


def quote_ident(name):
    if not _IDENT.fullmatch(name):
        raise ValueError(f"Unsafe identifier: {name!r}")
    return '"' + name + '"'


# ------------------------------------------------------------------ COPY text format

def _float_text(value):
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    return repr(float(value))                  # shortest exact round trip, keeps -0.0


def copy_value(value, logical):
    """One field in PostgreSQL COPY text format (NULL is \\N, backslash escapes)."""
    if value is None:
        return "\\N"
    if logical == "VARCHAR":
        if not isinstance(value, str):
            raise UnsupportedValue("VARCHAR value is not text")
        if "\x00" in value:
            raise UnsupportedValue("Text containing NUL cannot be stored in PostgreSQL")
        return (value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    if logical == "BOOLEAN":
        return "t" if value else "f"
    if logical == "DATE":
        return value.isoformat()
    if logical in ("INTEGER", "BIGINT", "HUGEINT"):
        return str(int(value))
    if logical == "DOUBLE":
        return _float_text(value)
    if logical.startswith("DECIMAL"):
        return format(Decimal(value), "f")
    raise UnsupportedValue(f"Unsupported type {logical}")


def copy_row(row, logicals):
    return "\t".join(copy_value(v, t) for v, t in zip(row, logicals)) + "\n"


# ------------------------------------------------------------------ canonical comparison

def from_postgres(value, logical):
    """Map a driver value back to the logical (DuckDB) Python type."""
    if value is None:
        return None
    if logical == "HUGEINT":
        return int(value)
    return value


def canonical_value(value, logical):
    if value is None:
        return b"N"
    if logical == "VARCHAR":
        data = value.encode("utf-8")
        return b"S" + str(len(data)).encode() + b":" + data
    if logical == "BOOLEAN":
        if not isinstance(value, bool):
            raise UnsupportedValue("BOOLEAN value is not a bool")
        return b"T" if value else b"F"
    if logical == "DATE":
        if not isinstance(value, date):
            raise UnsupportedValue("DATE value is not a date")
        return b"D" + value.isoformat().encode()
    if logical in ("INTEGER", "BIGINT", "HUGEINT"):
        if isinstance(value, Decimal):
            if value != value.to_integral_value():
                raise UnsupportedValue("Integer column carries a fraction")
            value = int(value)
        return b"I" + str(int(value)).encode()
    if logical == "DOUBLE":
        return b"F" + float(value).hex().encode()          # exact, distinguishes -0.0 / nan / inf
    if logical.startswith("DECIMAL"):
        scale = int(_DECIMAL.fullmatch(logical).group(2))
        exact = Decimal(value)
        if exact != exact.quantize(Decimal(1).scaleb(-scale)):
            raise UnsupportedValue("Decimal value exceeds its declared scale")
        return b"M" + format(exact.quantize(Decimal(1).scaleb(-scale)), "f").encode()
    raise UnsupportedValue(f"Unsupported type {logical}")


class RowDigest:
    """Ordered SHA-256 over canonical typed rows (order fixed by ``order_by``)."""

    def __init__(self):
        self._hash = hashlib.sha256()
        self.rows = 0

    def update(self, row, logicals):
        encoded = b"\x1f".join(canonical_value(v, t) for v, t in zip(row, logicals))
        self._hash.update(str(len(encoded)).encode() + b":" + encoded + b"\x1e")
        self.rows += 1

    def hexdigest(self):
        return self._hash.hexdigest()


def order_by(spec):
    """Deterministic total order over the declared unique business key (ASC, NULLS LAST).

    Keys are checked unique before export and contain no floating-point columns:
    DuckDB returns -0.0 as 0.0 for a DOUBLE sort key, so sorting on value columns
    would silently lose the sign. Text uses code-point order in both engines
    (DuckDB binary, PostgreSQL "C").
    """
    return ", ".join(f"{quote_ident(c)} ASC NULLS LAST" for c in spec["key"])
