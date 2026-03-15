"""
SQL syntax / structural rewrite rules: Snowflake → Databricks.
"""
from __future__ import annotations

import re
from .rule_engine import Rule

FLAGS = re.IGNORECASE | re.MULTILINE | re.DOTALL


SYNTAX_RULES: list[Rule] = [

    # --- SELECT TOP N → LIMIT N -----------------------------------------------
    # Handled in converter (appends LIMIT at end of statement)
    Rule(
        name="SELECT_TOP",
        pattern=re.compile(r'\bSELECT\s+TOP\s+(\d+)\b', FLAGS),
        replacement=r'SELECT /* TOP_\1_MOVED_TO_LIMIT */',
        confidence="medium",
        note="SELECT TOP N → SELECT … LIMIT N; LIMIT appended at statement end",
    ),

    # --- Time travel: AT / BEFORE → Delta time travel -------------------------
    Rule(
        name="TIME_TRAVEL_AT_TIMESTAMP",
        pattern=re.compile(
            r'\bAT\s*\(\s*TIMESTAMP\s*=>\s*([^)]+)\)',
            FLAGS,
        ),
        replacement=r'TIMESTAMP AS OF \1',
        confidence="high",
        note="Snowflake AT(TIMESTAMP=>) → Delta TIMESTAMP AS OF",
    ),
    Rule(
        name="TIME_TRAVEL_AT_OFFSET",
        pattern=re.compile(r'\bAT\s*\(\s*OFFSET\s*=>\s*([^)]+)\)', FLAGS),
        replacement=r'TIMESTAMP AS OF TIMESTAMPADD(SECOND, \1, CURRENT_TIMESTAMP())',
        confidence="medium",
        note="AT(OFFSET=>) converted to TIMESTAMP AS OF with offset",
    ),
    Rule(
        name="TIME_TRAVEL_AT_STATEMENT",
        pattern=re.compile(r'\bAT\s*\(\s*STATEMENT\s*=>\s*[^)]+\)', FLAGS),
        replacement='/* TIME_TRAVEL_REMOVED: AT(STATEMENT=>) has no Databricks equivalent */',
        confidence="medium",
        note="Statement-based time travel not supported in Delta",
    ),
    Rule(
        name="TIME_TRAVEL_BEFORE",
        pattern=re.compile(r'\bBEFORE\s*\(\s*(?:TIMESTAMP|OFFSET|STATEMENT)\s*=>[^)]+\)', FLAGS),
        replacement='/* TIME_TRAVEL_REMOVED: BEFORE clause not supported — use VERSION AS OF N-1 */',
        confidence="medium",
    ),

    # --- SAMPLE / TABLESAMPLE ------------------------------------------------
    Rule(
        name="SAMPLE_ROWS",
        pattern=re.compile(r'\bSAMPLE\s*\(\s*(\d+)\s+ROWS\s*\)', FLAGS),
        replacement=r'TABLESAMPLE (\1 ROWS)',
        confidence="high",
    ),
    Rule(
        name="SAMPLE_PERCENT",
        pattern=re.compile(r'\bSAMPLE\s*\(\s*(\d+(?:\.\d+)?)\s*\)', FLAGS),
        replacement=r'TABLESAMPLE (\1 PERCENT)',
        confidence="high",
    ),

    # --- Dollar-sign positional column refs ($1, $2, …) ----------------------
    Rule(
        name="POSITIONAL_COLUMN_REF",
        pattern=re.compile(r'\$(\d+)\b', FLAGS),
        replacement=r'col\1  /* MANUAL_REVIEW: positional ref $\1 → rename to actual column */',
        confidence="low",
        note="Positional column references not supported; replace with column names",
    ),

    # --- $$ string delimiter → single-quoted string ---------------------------
    Rule(
        name="DOLLAR_DOLLAR_DELIMITER",
        pattern=re.compile(r'\$\$(.+?)\$\$', re.DOTALL | re.IGNORECASE),
        replacement=lambda m: "'" + m.group(1).replace("'", "''") + "'",
        confidence="high",
        note="$$ string delimiters converted to single-quoted strings",
    ),

    # --- CONNECT BY → escalate to Claude (no Databricks equivalent) -----------
    Rule(
        name="CONNECT_BY",
        pattern=re.compile(r'\bCONNECT\s+BY\b', FLAGS),
        replacement=None,   # Escalate to Claude
        confidence="low",
        note="CONNECT BY not supported in Databricks — convert to recursive CTE via AI",
    ),

    # --- MATCH_RECOGNIZE → escalate to Claude --------------------------------
    Rule(
        name="MATCH_RECOGNIZE",
        pattern=re.compile(r'\bMATCH_RECOGNIZE\s*\(', FLAGS),
        replacement=None,
        confidence="low",
        note="MATCH_RECOGNIZE has no Databricks equivalent — MANUAL_REVIEW",
    ),

    # --- CHANGES (CDC clause) ------------------------------------------------
    Rule(
        name="CHANGES_CLAUSE",
        pattern=re.compile(r'\bCHANGES\s*\(\s*INFORMATION\s*=>\s*DEFAULT\s*\)', FLAGS),
        replacement='/* CHANGES_REMOVED: Use Delta Change Data Feed instead */',
        confidence="high",
    ),

    # --- FLATTEN table function (non-LATERAL) --------------------------------
    Rule(
        name="TABLE_FLATTEN",
        pattern=re.compile(r'\bTABLE\s*\(\s*FLATTEN\s*\(([^)]+)\)\s*\)', FLAGS),
        replacement=r'LATERAL VIEW EXPLODE(\1) _t AS value',
        confidence="medium",
    ),
]
