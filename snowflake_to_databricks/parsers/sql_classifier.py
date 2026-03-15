"""
Classify a SQL statement into its object type and detect procedure language.
"""
from __future__ import annotations

import re
from snowflake_to_databricks.models import ObjectType, ProcedureLanguage

_FLAGS = re.IGNORECASE | re.MULTILINE


_PATTERNS: list[tuple[ObjectType, re.Pattern]] = [
    (ObjectType.PROCEDURE,          re.compile(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?PROCEDURE\b', _FLAGS)),
    (ObjectType.FUNCTION,           re.compile(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\b', _FLAGS)),
    (ObjectType.MATERIALIZED_VIEW,  re.compile(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?MATERIALIZED\s+VIEW\b', _FLAGS)),
    (ObjectType.VIEW,               re.compile(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:SECURE\s+)?VIEW\b', _FLAGS)),
    (ObjectType.SEQUENCE,           re.compile(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?SEQUENCE\b', _FLAGS)),
    (ObjectType.STREAM,             re.compile(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?STREAM\b', _FLAGS)),
    (ObjectType.TASK,               re.compile(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?TASK\b', _FLAGS)),
    (ObjectType.STAGE,              re.compile(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EXTERNAL\s+)?STAGE\b', _FLAGS)),
    (ObjectType.TABLE,              re.compile(
        r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TRANSIENT\s+|TEMPORARY\s+|VOLATILE\s+)?TABLE\b', _FLAGS
    )),
    (ObjectType.DML,                re.compile(r'^\s*(SELECT|WITH|INSERT|UPDATE|DELETE|MERGE)\b', _FLAGS)),
]

_LANG_PATTERNS: dict[ProcedureLanguage, re.Pattern] = {
    ProcedureLanguage.JAVASCRIPT: re.compile(r'\bLANGUAGE\s+JAVASCRIPT\b', _FLAGS),
    ProcedureLanguage.PYTHON:     re.compile(r'\bLANGUAGE\s+PYTHON\b', _FLAGS),
    ProcedureLanguage.JAVA:       re.compile(r'\bLANGUAGE\s+JAVA\b', _FLAGS),
    ProcedureLanguage.SCALA:      re.compile(r'\bLANGUAGE\s+SCALA\b', _FLAGS),
    ProcedureLanguage.SQL:        re.compile(r'\bLANGUAGE\s+SQL\b', _FLAGS),
}


def classify(sql: str) -> ObjectType:
    for obj_type, pattern in _PATTERNS:
        if pattern.search(sql):
            return obj_type
    return ObjectType.UNKNOWN


def detect_procedure_language(sql: str) -> ProcedureLanguage:
    for lang, pattern in _LANG_PATTERNS.items():
        if pattern.search(sql):
            return lang
    # Snowflake Scripting uses BEGIN/END without LANGUAGE keyword
    if re.search(r'\bBEGIN\b.+\bEND\b', sql, re.IGNORECASE | re.DOTALL):
        return ProcedureLanguage.SQL
    return ProcedureLanguage.UNKNOWN
