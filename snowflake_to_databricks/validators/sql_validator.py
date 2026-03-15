"""
SQL Validator: 2-layer validation for converted Databricks SQL.
Layer 1 — sqlglot parse in Databricks dialect
Layer 2 — residual Snowflake pattern regex scan
"""
from __future__ import annotations

import re
from typing import Optional

try:
    import sqlglot
    import sqlglot.errors
    SQLGLOT_AVAILABLE = True
except ImportError:
    SQLGLOT_AVAILABLE = False

from snowflake_to_databricks.models import Severity, ValidationIssue, ValidationResult

# Patterns that indicate unconverted Snowflake syntax
_RESIDUAL_PATTERNS: list[tuple[str, str, Severity]] = [
    (r'\bNVL\s*\(',                     "NVL() not converted → COALESCE()",             Severity.ERROR),
    (r'\bZEROIFNULL\s*\(',             "ZEROIFNULL() not converted → COALESCE(x,0)",   Severity.ERROR),
    (r'\bIFNULL\s*\(',                  "IFNULL() not converted → COALESCE()",          Severity.ERROR),
    (r'\bARRAY_AGG\s*\(',              "ARRAY_AGG() not converted → COLLECT_LIST()",    Severity.ERROR),
    (r'\bOBJECT_CONSTRUCT\s*\(',       "OBJECT_CONSTRUCT not converted → NAMED_STRUCT", Severity.ERROR),
    (r'\bPARSE_JSON\s*\(',             "PARSE_JSON not converted → FROM_JSON(str, schema)", Severity.ERROR),
    (r'\bLATERAL\s+FLATTEN\s*\(',      "LATERAL FLATTEN not converted → LATERAL VIEW EXPLODE", Severity.ERROR),
    (r'\bLANGUAGE\s+JAVASCRIPT\b',     "JavaScript procedure body not converted",        Severity.ERROR),
    (r'\bCONNECT\s+BY\b',             "CONNECT BY not supported in Databricks",         Severity.ERROR),
    (r'\$\$',                           "$$ delimiter still present",                    Severity.ERROR),
    (r'\bTIMESTAMP_TZ\b',             "TIMESTAMP_TZ converted to TIMESTAMP (offset lost)", Severity.WARNING),
    (r'\bTIMESTAMP_LTZ\b',            "TIMESTAMP_LTZ converted to TIMESTAMP",           Severity.WARNING),
    (r'\bGEOGRAPHY\b',                "GEOGRAPHY type not fully converted",             Severity.WARNING),
    (r'\bGEOMETRY\b',                  "GEOMETRY type not fully converted",              Severity.WARNING),
    (r'@\w+',                           "Snowflake stage reference (@name) not converted", Severity.WARNING),
    (r'\bMATCH_RECOGNIZE\s*\(',        "MATCH_RECOGNIZE has no Databricks equivalent",  Severity.WARNING),
    (r'\bCREATE\s+STREAM\b',          "CREATE STREAM not converted to CDF",             Severity.WARNING),
    (r'\bCREATE\s+TASK\b',            "CREATE TASK not converted to Workflow",           Severity.WARNING),
    (r'\bCREATE\s+PIPE\b',            "CREATE PIPE not converted to Auto Loader",       Severity.WARNING),
    (r'\bMANUAL_REVIEW\b',            "Manual review required for this pattern",         Severity.WARNING),
    (r'\bTYPEOF\s*\(',                "TYPEOF has no Databricks equivalent",             Severity.WARNING),
    (r'\bSEARCH\s+OPTIMIZATION\b',    "SEARCH OPTIMIZATION not converted",              Severity.WARNING),
]


class SQLValidator:

    def validate(self, sql: str) -> ValidationResult:
        issues: list[ValidationIssue] = []

        # Layer 1: sqlglot parse
        if SQLGLOT_AVAILABLE:
            issues.extend(self._sqlglot_validate(sql))

        # Layer 2: residual Snowflake pattern scan
        issues.extend(self._residual_scan(sql))

        has_errors = any(i.severity == Severity.ERROR for i in issues)
        return ValidationResult(is_valid=not has_errors, issues=issues)

    def _sqlglot_validate(self, sql: str) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        try:
            statements = sqlglot.parse(sql, dialect="databricks", error_level=sqlglot.ErrorLevel.WARN)
            for stmt in statements:
                if stmt is None:
                    continue
                for err in getattr(stmt, 'errors', []):
                    issues.append(ValidationIssue(
                        severity=Severity.ERROR,
                        message=f"sqlglot parse error: {err}",
                    ))
        except Exception as exc:
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message=f"sqlglot validation failed: {exc}",
            ))
        return issues

    def _residual_scan(self, sql: str) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for pattern, message, severity in _RESIDUAL_PATTERNS:
            for match in re.finditer(pattern, sql, re.IGNORECASE | re.MULTILINE):
                line_num = sql[:match.start()].count('\n') + 1
                issues.append(ValidationIssue(
                    severity=severity,
                    message=message,
                    line_number=line_num,
                    pattern=pattern,
                ))
        return issues
