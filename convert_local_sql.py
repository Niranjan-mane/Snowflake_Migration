#!/usr/bin/env python3
"""
Local SQL conversion script — no Snowflake/Databricks connections required.

Reads Snowflake SQL from input_sql/, converts it using rule-based engine,
and writes Databricks-compatible SQL to converted_sql/.

Usage:
    python convert_local_sql.py [--input input_sql/snowflake_fraud_detection.sql]
                                [--output converted_sql/databricks_fraud_detection.sql]
"""
import sys
import os
import argparse
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from snowflake_to_databricks.converters.table_converter import TableConverter
from snowflake_to_databricks.converters.view_converter import ViewConverter
from snowflake_to_databricks.converters.dml_converter import DMLConverter
from snowflake_to_databricks.converters.base import BaseConverter
from snowflake_to_databricks.parsers.sql_classifier import classify
from snowflake_to_databricks.parsers.statement_splitter import split_statements
from snowflake_to_databricks.models import ObjectType, SnowflakeObject


def convert_file(input_path: str, output_path: str) -> None:
    in_p = Path(input_path)
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    raw_sql = in_p.read_text(encoding="utf-8")

    converters = {
        ObjectType.TABLE:   TableConverter(),
        ObjectType.VIEW:    ViewConverter(),
        ObjectType.DML:     DMLConverter(),
    }
    default_conv = BaseConverter()

    statements = split_statements(raw_sql)
    converted_parts = []
    summary = []

    for i, stmt in enumerate(statements):
        stmt = stmt.strip()
        if not stmt:
            continue

        obj_type = classify(stmt)
        converter = converters.get(obj_type, default_conv)

        # Build a minimal source object
        name = _extract_name(stmt, obj_type) or f"stmt_{i+1}"
        source = SnowflakeObject(
            name=name,
            schema="FRAUD",
            database="FRAUD_DETECTION",
            object_type=obj_type,
            ddl=stmt,
        )

        result = converter.convert(source)
        converted_parts.append(result.converted_sql)

        change_strs = [f"  - [{c.rule_name}] {c.original[:60]!r} → {c.replacement[:60]!r}" for c in result.changes]
        summary.append(
            f"[{i+1:02d}] {obj_type.value.upper():12s} {name:40s}  changes={len(result.changes)}"
        )
        for cs in change_strs:
            summary.append(cs)

    output_sql = "\n\n".join(converted_parts)
    out_p.write_text(output_sql, encoding="utf-8")

    print(f"\n{'='*70}")
    print(f"  Snowflake → Databricks SQL Conversion")
    print(f"{'='*70}")
    print(f"  Input  : {input_path}")
    print(f"  Output : {output_path}")
    print(f"  Statements converted: {len(converted_parts)}")
    print(f"{'='*70}\n")
    print("Conversion details:")
    for line in summary:
        print(line)
    print(f"\n✓ Converted SQL written to: {output_path}\n")


def _extract_name(stmt: str, obj_type: ObjectType) -> str:
    import re
    patterns = {
        ObjectType.TABLE: re.compile(
            r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:TRANSIENT\s+|TEMPORARY\s+)?TABLE\s+(\S+)', re.I
        ),
        ObjectType.VIEW: re.compile(
            r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:SECURE\s+)?(?:MATERIALIZED\s+)?VIEW\s+(\S+)', re.I
        ),
    }
    pat = patterns.get(obj_type)
    if pat:
        m = pat.search(stmt)
        if m:
            return m.group(1).split('.')[-1]
    return ""


def main():
    parser = argparse.ArgumentParser(description="Convert Snowflake SQL → Databricks SQL (offline)")
    parser.add_argument(
        "--input", default="input_sql/snowflake_fraud_detection.sql",
        help="Path to input Snowflake SQL file"
    )
    parser.add_argument(
        "--output", default="converted_sql/databricks_fraud_detection.sql",
        help="Path to write converted Databricks SQL"
    )
    args = parser.parse_args()
    convert_file(args.input, args.output)


if __name__ == "__main__":
    main()
