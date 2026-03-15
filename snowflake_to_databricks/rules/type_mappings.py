"""
Data type conversion rules: Snowflake → Databricks.
"""
from __future__ import annotations

import re
from .rule_engine import Rule

FLAGS = re.IGNORECASE | re.MULTILINE

TYPE_RULES: list[Rule] = [
    # --- Numeric ---
    Rule(
        name="NUMBER_with_precision",
        pattern=re.compile(r'\bNUMBER\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', FLAGS),
        replacement=r'DECIMAL(\1, \2)',
        confidence="high",
    ),
    Rule(
        name="NUMERIC_with_precision",
        pattern=re.compile(r'\bNUMERIC\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', FLAGS),
        replacement=r'DECIMAL(\1, \2)',
        confidence="high",
    ),
    Rule(
        name="NUMBER_bare",
        pattern=re.compile(r'\bNUMBER\b(?!\s*\()', FLAGS),
        replacement='DECIMAL(38, 0)',
        confidence="high",
    ),
    Rule(
        name="BYTEINT",
        pattern=re.compile(r'\bBYTEINT\b', FLAGS),
        replacement='TINYINT',
        confidence="high",
    ),
    Rule(
        name="FLOAT8",
        pattern=re.compile(r'\bFLOAT8\b', FLAGS),
        replacement='DOUBLE',
        confidence="high",
    ),
    Rule(
        name="FLOAT4",
        pattern=re.compile(r'\bFLOAT4\b', FLAGS),
        replacement='FLOAT',
        confidence="high",
    ),
    Rule(
        name="DOUBLE_PRECISION",
        pattern=re.compile(r'\bDOUBLE\s+PRECISION\b', FLAGS),
        replacement='DOUBLE',
        confidence="high",
    ),
    Rule(
        name="REAL_type",
        pattern=re.compile(r'\bREAL\b', FLAGS),
        replacement='FLOAT',
        confidence="high",
    ),

    # --- String ---
    Rule(
        name="VARCHAR_bare",
        pattern=re.compile(r'\bVARCHAR\b(?!\s*\()', FLAGS),
        replacement='STRING',
        confidence="high",
    ),
    Rule(
        name="TEXT_type",
        pattern=re.compile(r'\bTEXT\b', FLAGS),
        replacement='STRING',
        confidence="high",
    ),
    Rule(
        name="NVARCHAR",
        pattern=re.compile(r'\bNVARCHAR\s*\(\s*(\d+)\s*\)', FLAGS),
        replacement=r'VARCHAR(\1)',
        confidence="high",
    ),
    Rule(
        name="CHARACTER_type",
        pattern=re.compile(r'\bCHARACTER\b(?!\s+VARYING)', FLAGS),
        replacement='CHAR',
        confidence="high",
    ),

    # --- Timestamp / Date ---
    Rule(
        name="TIMESTAMP_NTZ",
        pattern=re.compile(r'\bTIMESTAMP_NTZ(?:\s*\(\d+\))?', FLAGS),
        replacement='TIMESTAMP_NTZ',
        confidence="high",
        note="TIMESTAMP_NTZ is supported natively in Databricks DBR 10.4+",
    ),
    Rule(
        name="TIMESTAMP_LTZ",
        pattern=re.compile(r'\bTIMESTAMP_LTZ(?:\s*\(\d+\))?', FLAGS),
        replacement='TIMESTAMP',
        confidence="high",
        note="TIMESTAMP_LTZ → TIMESTAMP; timezone info comes from session setting",
    ),
    Rule(
        name="TIMESTAMP_TZ",
        pattern=re.compile(r'\bTIMESTAMP_TZ(?:\s*\(\d+\))?', FLAGS),
        replacement='TIMESTAMP',
        confidence="medium",
        note="TIMESTAMP_TZ → TIMESTAMP; timezone offset is LOST — review if precision needed",
    ),
    Rule(
        name="DATETIME_type",
        pattern=re.compile(r'\bDATETIME\b', FLAGS),
        replacement='TIMESTAMP',
        confidence="high",
    ),
    Rule(
        name="TIME_type",
        pattern=re.compile(r'\bTIME\b(?!\s*STAMP)', FLAGS),
        replacement='STRING',
        confidence="medium",
        note="Databricks has no native TIME type — stored as STRING 'HH:mm:ss'",
    ),

    # --- Semi-structured ---
    Rule(
        name="VARIANT_type",
        pattern=re.compile(r'\bVARIANT\b', FLAGS),
        replacement='STRING',
        confidence="medium",
        note="VARIANT stored as JSON STRING. Use VARIANT type if on DBR 15+",
    ),
    Rule(
        name="OBJECT_type",
        pattern=re.compile(r'\bOBJECT\b', FLAGS),
        replacement='MAP<STRING, STRING>',
        confidence="medium",
        note="OBJECT → MAP<STRING,STRING>; review key/value types",
    ),
    Rule(
        name="ARRAY_type",
        pattern=re.compile(r'\bARRAY\b(?!\s*<)', FLAGS),
        replacement='ARRAY<STRING>',
        confidence="medium",
        note="ARRAY → ARRAY<STRING>; review element type",
    ),

    # --- Geospatial ---
    Rule(
        name="GEOGRAPHY_type",
        pattern=re.compile(r'\bGEOGRAPHY\b', FLAGS),
        replacement='STRING',
        confidence="low",
        note="GEOGRAPHY → STRING (WKT format); no native geospatial type in Databricks — MANUAL_REVIEW",
    ),
    Rule(
        name="GEOMETRY_type",
        pattern=re.compile(r'\bGEOMETRY\b', FLAGS),
        replacement='STRING',
        confidence="low",
        note="GEOMETRY → STRING (WKT format); no native geospatial type in Databricks — MANUAL_REVIEW",
    ),
]
