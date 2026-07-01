"""
connection.py
-------------
BUILD-TIME step. Connects to Oracle, introspects the schema, and writes
schema/schema.json — the single artifact the runtime engine trusts.

Usage:
    1. cp config.example.yaml config.yaml   and fill in your DB details
    2. pip install oracledb
    3. python connection.py                 # writes schema/schema.json
       python connection.py --out other.json --config prod.yaml

It reads from Oracle's data dictionary:
    all_tables / all_tab_comments   -> tables + table comments
    all_tab_columns                 -> columns + data types
    all_col_comments                -> the human glossary the engine matches on
    all_constraints / all_cons_columns -> primary keys and foreign keys

python-oracledb runs in "thin" mode by default — no Oracle Instant Client needed.
"""

import argparse
import json
import os
import sys

from db_config import load_config, DBConfig


def _format_type(data_type, length, precision, scale) -> str:
    dt = (data_type or "").upper()
    if dt in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR", "RAW") and length:
        return f"{dt}({int(length)})"
    if dt == "NUMBER":
        if precision and scale:
            return f"NUMBER({int(precision)},{int(scale)})"
        if precision:
            return f"NUMBER({int(precision)})"
        return "NUMBER"
    return dt


def introspect(cfg: DBConfig) -> dict:
    import oracledb   # imported here so `--help` works without it installed

    print(f"Connecting to {cfg.dsn} as {cfg.user} ...", file=sys.stderr)
    conn = oracledb.connect(user=cfg.user, password=cfg.password, dsn=cfg.dsn)
    owner = cfg.owner
    cur = conn.cursor()

    table_filter = ""
    binds = {"owner": owner}
    if cfg.tables:
        names = [t.upper() for t in cfg.tables]
        placeholders = ", ".join(f":t{i}" for i in range(len(names)))
        table_filter = f" AND table_name IN ({placeholders})"
        binds.update({f"t{i}": n for i, n in enumerate(names)})

    # --- tables + comments ---
    cur.execute(
        f"SELECT table_name FROM all_tables WHERE owner = :owner{table_filter} "
        "ORDER BY table_name", binds)
    tables = {t[0]: {"comment": "", "columns": {}, "foreign_keys": []}
              for t in cur.fetchall()}

    cur.execute(
        "SELECT table_name, comments FROM all_tab_comments "
        "WHERE owner = :owner AND table_type = 'TABLE'", {"owner": owner})
    for tname, comment in cur.fetchall():
        if tname in tables:
            tables[tname]["comment"] = comment or ""

    # --- columns ---
    cur.execute(
        "SELECT table_name, column_name, data_type, data_length, "
        "data_precision, data_scale FROM all_tab_columns "
        "WHERE owner = :owner ORDER BY table_name, column_id", {"owner": owner})
    for tname, cname, dtype, dlen, dprec, dscale in cur.fetchall():
        if tname in tables:
            tables[tname]["columns"][cname] = {
                "type": _format_type(dtype, dlen, dprec, dscale),
                "comment": "",
            }

    # --- column comments (the glossary) ---
    cur.execute(
        "SELECT table_name, column_name, comments FROM all_col_comments "
        "WHERE owner = :owner", {"owner": owner})
    for tname, cname, comment in cur.fetchall():
        if tname in tables and cname in tables[tname]["columns"]:
            tables[tname]["columns"][cname]["comment"] = comment or ""

    # --- primary keys ---
    cur.execute(
        "SELECT acc.table_name, acc.column_name "
        "FROM all_constraints ac "
        "JOIN all_cons_columns acc ON ac.owner = acc.owner "
        "  AND ac.constraint_name = acc.constraint_name "
        "WHERE ac.owner = :owner AND ac.constraint_type = 'P'", {"owner": owner})
    for tname, cname in cur.fetchall():
        if tname in tables and cname in tables[tname]["columns"]:
            tables[tname]["columns"][cname]["pk"] = True

    # --- foreign keys ---
    cur.execute(
        "SELECT acc.table_name, acc.column_name, "
        "       rcc.table_name AS r_table, rcc.column_name AS r_column "
        "FROM all_constraints ac "
        "JOIN all_cons_columns acc ON ac.owner = acc.owner "
        "  AND ac.constraint_name = acc.constraint_name "
        "JOIN all_cons_columns rcc ON ac.r_owner = rcc.owner "
        "  AND ac.r_constraint_name = rcc.constraint_name "
        "  AND acc.position = rcc.position "
        "WHERE ac.owner = :owner AND ac.constraint_type = 'R'", {"owner": owner})
    for tname, cname, rtable, rcolumn in cur.fetchall():
        if tname in tables:
            tables[tname]["foreign_keys"].append({
                "column": cname, "ref_table": rtable, "ref_column": rcolumn})

    cur.close()
    conn.close()

    return {
        "schema_name": owner,
        "generated_note": f"Introspected from Oracle owner {owner} by connection.py",
        "tables": tables,
    }


def main():
    ap = argparse.ArgumentParser(description="Introspect Oracle -> schema.json")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default="schema/schema.json")
    args = ap.parse_args()

    cfg = load_config(args.config)
    missing = cfg.missing()
    if missing:
        print(f"ERROR: missing config values: {', '.join(missing)}\n"
              f"Fill in {args.config} (see config.example.yaml) or set env vars.",
              file=sys.stderr)
        sys.exit(2)

    try:
        schema = introspect(cfg)
    except ImportError:
        print("ERROR: python-oracledb not installed.  pip install oracledb",
              file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"ERROR: introspection failed: {e}", file=sys.stderr)
        sys.exit(4)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(schema, f, indent=2)

    ncols = sum(len(t["columns"]) for t in schema["tables"].values())
    nfks = sum(len(t["foreign_keys"]) for t in schema["tables"].values())
    print(f"Wrote {args.out}: {len(schema['tables'])} tables, "
          f"{ncols} columns, {nfks} foreign keys.")


if __name__ == "__main__":
    main()
