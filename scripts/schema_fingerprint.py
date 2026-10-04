import argparse
import json
import re
import sys
from typing import Any, Dict

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql


def clean_name(name: str) -> str:
    if not name:
        return name
    name = name.replace('"', '')
    if name.startswith("public."):
        name = name[7:]
    return name


def canonize_default(default: str) -> str:
    if not default:
        return default
    default = default.strip()
    while default.startswith("(") and default.endswith(")"):
        default = default[1:-1].strip()
    if default.lower() in ("current_timestamp", "now()"):
        return "now()"
    text_cast = re.match(r"^('.*?')::(text|character varying)$", default, re.IGNORECASE)
    if text_cast:
        return f"{text_cast.group(1)}::text"
    return default


def get_schema_fingerprint(url: str) -> Dict[str, Any]:
    if not url.startswith("postgresql"):
        print("URL must be postgresql", file=sys.stderr)
        sys.exit(1)
    if not (url.endswith("_test") or url.endswith("_staging_clone")):
        print("Database name must end with _test or _staging_clone", file=sys.stderr)
        sys.exit(1)

    engine = create_engine(url)
    inspector = inspect(engine)
    
    fingerprint = {
        "tables": {},
        "alembic_version": []
    }

    try:
        if inspector.has_table("alembic_version"):
            with engine.connect() as conn:
                version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
                fingerprint["alembic_version"] = [version] if version else []
    except Exception:
        pass

    tables = sorted(inspector.get_table_names())
    for table in tables:
        if table == "alembic_version":
            continue
            
        t_info = {
            "columns": {},
            "pk": [],
            "fk": [],
            "uniques": [],
            "indexes": []
        }
        
        for col in inspector.get_columns(table):
            ctype = col["type"].compile(dialect=postgresql.dialect()).lower()
            if hasattr(col["type"], "enums"):
                ctype += f" ({','.join(col['type'].enums)})"
            
            t_info["columns"][col["name"]] = {
                "type": ctype,
                "nullable": col["nullable"],
                "default": canonize_default(col.get("default", ""))
            }
            
        pk = inspector.get_pk_constraint(table)
        if pk and pk.get("constrained_columns"):
            t_info["pk"] = sorted([clean_name(c) for c in pk["constrained_columns"]])
            
        for fk in inspector.get_foreign_keys(table):
            fk_info = {
                "constrained_columns": sorted([clean_name(c) for c in fk["constrained_columns"]]),
                "referred_table": clean_name(fk["referred_table"]),
                "referred_columns": sorted([clean_name(c) for c in fk["referred_columns"]]),
                "options": fk.get("options", {})
            }
            t_info["fk"].append(fk_info)
        t_info["fk"] = sorted(t_info["fk"], key=lambda x: str(x))
            
        for uq in inspector.get_unique_constraints(table):
            t_info["uniques"].append(sorted([clean_name(c) for c in uq["column_names"]]))
        t_info["uniques"] = sorted(t_info["uniques"])
            
        for idx in inspector.get_indexes(table):
            idx_info = {
                "name": clean_name(idx["name"]),
                "columns": sorted([clean_name(c) for c in idx["column_names"]]),
                "unique": idx["unique"]
            }
            t_info["indexes"].append(idx_info)
        t_info["indexes"] = sorted(t_info["indexes"], key=lambda x: str(x))
            
        fingerprint["tables"][table] = t_info
        
    return fingerprint


def dict_diff(d1: dict, d2: dict) -> dict:
    diff = {}
    for k in set(d1.keys()).union(d2.keys()):
        if k not in d1:
            diff[k] = {"old": None, "new": d2[k]}
        elif k not in d2:
            diff[k] = {"old": d1[k], "new": None}
        elif isinstance(d1[k], dict) and isinstance(d2[k], dict):
            sub = dict_diff(d1[k], d2[k])
            if sub:
                diff[k] = sub
        elif d1[k] != d2[k]:
            diff[k] = {"old": d1[k], "new": d2[k]}
    return diff


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-url", required=True)
    parser.add_argument("--candidate-url", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    ref = get_schema_fingerprint(args.reference_url)
    cand = get_schema_fingerprint(args.candidate_url)
    
    diff = dict_diff(ref, cand)
    diff_json = json.dumps(diff, indent=2)
    
    with open(args.output, "w") as f:
        f.write(diff_json if diff else "{}")
        
    if diff:
        sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    main()
