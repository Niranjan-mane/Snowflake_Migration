"""
Table DDL converter.
Always adds USING DELTA and Delta Lake table properties.
"""
from __future__ import annotations

import re
from typing import Optional

from snowflake_to_databricks.models import Change, ObjectType, SnowflakeObject
from snowflake_to_databricks.converters.base import BaseConverter
from snowflake_to_databricks.rules.rule_engine import RuleEngine


_USING_DELTA_RE = re.compile(r'\bUSING\s+DELTA\b', re.IGNORECASE)
_TBLPROPERTIES_RE = re.compile(r'\bTBLPROPERTIES\s*\(', re.IGNORECASE)
_CREATE_TABLE_END_RE = re.compile(
    r'(\)\s*)(?=\s*(?:USING|CLUSTER|PARTITIONED|TBLPROPERTIES|COMMENT|LOCATION|;|$))',
    re.IGNORECASE | re.DOTALL,
)
_DATA_RETENTION_RE = re.compile(
    r"'delta\.deletedFileRetentionDuration'\s*=\s*'interval\s+(\d+)\s+days'",
    re.IGNORECASE,
)
_TRANSIENT_RE = re.compile(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?TABLE\b', re.IGNORECASE)

# Fix: Databricks IDENTITY columns require BIGINT, not DECIMAL/NUMERIC
# Matches any DECIMAL/NUMERIC type immediately before GENERATED ALWAYS AS IDENTITY
_IDENTITY_TYPE_FIX_RE = re.compile(
    r'(?:DECIMAL|NUMERIC)\s*\(\d+,\s*\d+\)(\s+(?:NOT\s+NULL\s+)?GENERATED\s+ALWAYS\s+AS\s+IDENTITY)',
    re.IGNORECASE | re.MULTILINE,
)

# Fix: Snowflake virtual/computed column syntax:  col_name TYPE AS (expr)
# Databricks equivalent:                          col_name TYPE GENERATED ALWAYS AS (expr)
# Matches TYPE) AS ( but NOT CAST(x AS TYPE) or CREATE VIEW v AS (SELECT ...)
_SF_COMPUTED_COL_RE = re.compile(
    r'(\b(?:DECIMAL|NUMERIC|NUMBER|INT|INTEGER|BIGINT|SMALLINT|TINYINT|BYTEINT'
    r'|FLOAT4?|FLOAT8|DOUBLE(?:\s+PRECISION)?|REAL|BOOLEAN|DATE|DATETIME'
    r'|TIMESTAMP(?:_NTZ|_LTZ|_TZ)?|VARCHAR|CHAR|CHARACTER|STRING|TEXT|BINARY)'
    r'(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?)'
    r'\s+AS\s*\((?!\s*(?:SELECT|WITH)\b)',
    re.IGNORECASE | re.DOTALL,
)

# Detect DEFAULT values in column definitions (requires allowColumnDefaults feature)
_HAS_DEFAULT_RE = re.compile(r'\bDEFAULT\b', re.IGNORECASE)

# Pattern to find DECIMAL/NUMERIC GENERATED ALWAYS AS (expr) columns
_GENERATED_DECIMAL_COL_RE = re.compile(
    r'((?:DECIMAL|NUMERIC)\s*\(\s*\d+\s*,\s*\d+\s*\))\s+GENERATED\s+ALWAYS\s+AS\s+\(',
    re.IGNORECASE,
)


def _wrap_generated_decimal_cast(sql: str) -> str:
    """
    Wrap DECIMAL/NUMERIC generated column expressions in CAST to ensure type
    compatibility with Databricks (SQLSTATE 42621).

    Databricks requires the generated expression result type to exactly match
    the declared column type. Arithmetic on DECIMAL columns expands precision
    (e.g. DECIMAL(18,2) * DECIMAL(18,6) → DECIMAL(37,8)), which is incompatible
    with a DECIMAL(18,2) column. Wrapping in CAST enforces the declared type.
    Already-wrapped expressions are left unchanged to avoid double-wrapping.
    """
    parts: list[str] = []
    last = 0
    for m in _GENERATED_DECIMAL_COL_RE.finditer(sql):
        col_type = m.group(1)
        # Walk forward to find the matching closing paren
        depth = 1
        j = m.end()
        while j < len(sql) and depth > 0:
            if sql[j] == '(':
                depth += 1
            elif sql[j] == ')':
                depth -= 1
            j += 1
        expr = sql[m.end():j - 1]  # Expression body (without outer parens)
        if re.match(r'\s*CAST\s*\(', expr, re.IGNORECASE):
            # Already wrapped — keep as-is
            parts.append(sql[last:j])
        else:
            parts.append(sql[last:m.start()])
            parts.append(
                f'{col_type} GENERATED ALWAYS AS (CAST({expr.strip()} AS {col_type}))'
            )
        last = j
    parts.append(sql[last:])
    return ''.join(parts)


class TableConverter(BaseConverter):

    def __init__(
        self,
        rule_engine: Optional[RuleEngine] = None,
        add_auto_optimize: bool = True,
        default_retention_days: int = 7,
        is_transient: bool = False,
    ):
        super().__init__(rule_engine)
        self._add_auto_optimize = add_auto_optimize
        self._default_retention_days = default_retention_days
        self._is_transient = is_transient

    def _post_process(
        self, sql: str, source_object: SnowflakeObject
    ) -> tuple[str, list[Change]]:
        changes: list[Change] = []

        # Fix Snowflake computed/virtual columns: TYPE AS (expr) → TYPE GENERATED ALWAYS AS (expr)
        if _SF_COMPUTED_COL_RE.search(sql):
            sql = _SF_COMPUTED_COL_RE.sub(r'\1 GENERATED ALWAYS AS (', sql)
            changes.append(Change(
                rule_name="COMPUTED_COLUMN",
                original="col TYPE AS (expr)  [Snowflake virtual column]",
                replacement="col TYPE GENERATED ALWAYS AS (expr)",
                confidence="high",
                note="Snowflake virtual column syntax converted to Databricks GENERATED ALWAYS AS",
            ))
            # Wrap expressions in CAST to ensure the expression result type matches
            # the declared column type (Databricks SQLSTATE 42621)
            sql = _wrap_generated_decimal_cast(sql)

        # Fix IDENTITY column data types: Databricks requires BIGINT, not DECIMAL/NUMERIC
        if _IDENTITY_TYPE_FIX_RE.search(sql):
            sql = _IDENTITY_TYPE_FIX_RE.sub(r'BIGINT\1', sql)
            changes.append(Change(
                rule_name="IDENTITY_TYPE_BIGINT",
                original="DECIMAL(N,0) GENERATED ALWAYS AS IDENTITY",
                replacement="BIGINT GENERATED ALWAYS AS IDENTITY",
                confidence="high",
                note="Databricks IDENTITY columns require BIGINT data type (SQLSTATE: 428H2)",
            ))

        # Detect if original was a TRANSIENT table
        original_lower = source_object.ddl.lower()
        is_transient = 'transient' in original_lower

        # Determine retention days
        retention_days = 1 if is_transient else self._default_retention_days

        # Check if DATA_RETENTION was already converted (by ddl_rules)
        # Extract the days from the comment if present
        retention_match = re.search(
            r"interval\s+(\d+)\s+days", sql, re.IGNORECASE
        )
        if retention_match:
            retention_days = int(retention_match.group(1))

        # Detect if the table has DEFAULT column values
        has_defaults = bool(_HAS_DEFAULT_RE.search(sql))

        # Build TBLPROPERTIES block
        tblprops = self._build_tblproperties(retention_days, is_transient, has_defaults)

        # Add USING DELTA if not already present
        if not _USING_DELTA_RE.search(sql):
            sql = self._inject_using_delta(sql, tblprops)
            changes.append(Change(
                rule_name="ADD_USING_DELTA",
                original="(table without USING DELTA)",
                replacement="USING DELTA + autoOptimize TBLPROPERTIES",
                confidence="high",
                note="Delta Lake storage layer added",
            ))
        elif not _TBLPROPERTIES_RE.search(sql):
            # Has USING DELTA but no TBLPROPERTIES
            sql = sql.rstrip(';').rstrip() + f"\n{tblprops};"
            changes.append(Change(
                rule_name="ADD_TBLPROPERTIES",
                original="(missing TBLPROPERTIES)",
                replacement=tblprops,
                confidence="high",
            ))

        return sql, changes

    def _build_tblproperties(
        self, retention_days: int, is_transient: bool, has_defaults: bool = False
    ) -> str:
        props = [
            f"  'delta.deletedFileRetentionDuration' = 'interval {retention_days} days'",
        ]
        if has_defaults:
            # Required when any column has a DEFAULT value in Databricks Delta
            props.append("  'delta.feature.allowColumnDefaults' = 'supported'")
        if self._add_auto_optimize:
            props += [
                "  'delta.autoOptimize.optimizeWrite' = 'true'",
                "  'delta.autoOptimize.autoCompact'   = 'true'",
            ]
        if is_transient:
            props.append("  'delta.appendOnly' = 'false'")

        inner = ',\n'.join(props)
        return f"TBLPROPERTIES (\n{inner}\n)"

    def _inject_using_delta(self, sql: str, tblprops: str) -> str:
        """
        Insert USING DELTA + TBLPROPERTIES before the trailing semicolon.
        Handles CREATE TABLE (...) and CREATE TABLE ... AS SELECT.
        """
        sql = sql.rstrip()
        has_semicolon = sql.endswith(';')
        if has_semicolon:
            sql = sql[:-1].rstrip()

        sql = f"{sql}\nUSING DELTA\n{tblprops}"
        if has_semicolon:
            sql += ";"
        return sql
