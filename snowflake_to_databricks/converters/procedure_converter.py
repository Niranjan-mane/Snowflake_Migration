"""
Stored procedure converter.

Strategy:
  - SQL Scripting  → rule-based + AI for complex blocks
  - JavaScript     → AI converts to Python UDF or SQL procedure
  - Python         → adapt Snowpark → PySpark imports
  - Java/Scala     → AI + MANUAL_REVIEW
"""
from __future__ import annotations

import re
from typing import Optional

from snowflake_to_databricks.models import (
    Change, ConversionMethod, ConversionResult, ConversionStatus,
    ObjectType, ProcedureLanguage, SnowflakeObject,
)
from snowflake_to_databricks.converters.base import BaseConverter
from snowflake_to_databricks.rules.rule_engine import RuleEngine

# SQL scripting keyword rewrites
_SQL_SCRIPTING_RULES = [
    (re.compile(r'\bLET\s+(\w+)\s*:=\s*', re.IGNORECASE), r'SET VAR \1 = '),
    (re.compile(r'\bSNOWFLAKE\.EXECUTE\s*\(', re.IGNORECASE), 'EXECUTE IMMEDIATE ('),
    (re.compile(r'\bRESULTSET\b', re.IGNORECASE), '/* MANUAL_REVIEW: RESULTSET → use temp view */'),
]

_SNOWPARK_IMPORT_RE = re.compile(
    r'from\s+snowflake\.snowpark[^\n]*|import\s+snowflake\.snowpark[^\n]*',
    re.IGNORECASE,
)

_LANG_RE = re.compile(r'\bLANGUAGE\s+(JAVASCRIPT|PYTHON|JAVA|SCALA|SQL)\b', re.IGNORECASE)

# Required Databricks procedure syntax
_PROC_LANG_RE = re.compile(r'\bLANGUAGE\s+SQL\b', re.IGNORECASE)
_SQL_SECURITY_RE = re.compile(r'\bSQL\s+SECURITY\s+\w+\b', re.IGNORECASE)


class ProcedureConverter(BaseConverter):

    def __init__(self, rule_engine: Optional[RuleEngine] = None):
        super().__init__(rule_engine)

    def convert(
        self,
        source_object: SnowflakeObject,
        ai_client=None,
    ) -> ConversionResult:
        lang = source_object.procedure_language or ProcedureLanguage.UNKNOWN
        sql = source_object.ddl

        # Always needs AI for JavaScript, Java, Scala
        if lang in (ProcedureLanguage.JAVASCRIPT, ProcedureLanguage.JAVA, ProcedureLanguage.SCALA):
            if ai_client is not None:
                try:
                    converted_sql, tokens = ai_client.convert_procedure(
                        sql=sql,
                        language=lang.value,
                    )
                    return ConversionResult(
                        source_object=source_object,
                        original_sql=sql,
                        converted_sql=converted_sql,
                        object_type=ObjectType.PROCEDURE,
                        status=ConversionStatus.CONVERTED,
                        method=ConversionMethod.AI,
                        ai_used=True,
                        ai_tokens_used=tokens,
                    )
                except Exception as exc:
                    converted_sql = (
                        f"-- MANUAL_REVIEW: AI conversion failed: {exc}\n"
                        f"-- Language: {lang.value}\n"
                        + sql
                    )
                    return ConversionResult(
                        source_object=source_object,
                        original_sql=sql,
                        converted_sql=converted_sql,
                        object_type=ObjectType.PROCEDURE,
                        status=ConversionStatus.NEEDS_REVIEW,
                        method=ConversionMethod.MANUAL_REVIEW,
                    )
            else:
                converted_sql = (
                    f"-- MANUAL_REVIEW: {lang.value.upper()} stored procedure "
                    f"requires AI conversion.\n"
                    f"-- Enable --ai flag and set ANTHROPIC_API_KEY.\n"
                ) + sql
                return ConversionResult(
                    source_object=source_object,
                    original_sql=sql,
                    converted_sql=converted_sql,
                    object_type=ObjectType.PROCEDURE,
                    status=ConversionStatus.NEEDS_REVIEW,
                    method=ConversionMethod.MANUAL_REVIEW,
                )

        # For Python — adapt Snowpark imports
        if lang == ProcedureLanguage.PYTHON:
            sql = _SNOWPARK_IMPORT_RE.sub(
                '# MANUAL_REVIEW: Replace snowflake.snowpark with pyspark equivalents',
                sql,
            )

        # For SQL scripting — apply rule-based transformations
        result = self._engine.apply(sql)
        converted_sql = result.sql
        changes = []

        # Apply SQL scripting-specific rewrites
        for pattern, repl in _SQL_SCRIPTING_RULES:
            new_sql, found = pattern.subn(repl, converted_sql)
            if found:
                converted_sql = new_sql
                changes.append(Change(
                    rule_name="SQL_SCRIPTING",
                    original=str(pattern.pattern),
                    replacement=repl,
                    confidence="medium",
                ))

        # Ensure Databricks procedure has LANGUAGE SQL and SQL SECURITY INVOKER
        if not _PROC_LANG_RE.search(converted_sql):
            converted_sql = _LANG_RE.sub('LANGUAGE SQL', converted_sql)
        if not _SQL_SECURITY_RE.search(converted_sql):
            # Insert SQL SECURITY INVOKER after LANGUAGE SQL
            converted_sql = re.sub(
                r'(\bLANGUAGE\s+SQL\b)',
                r'\1\nSQL SECURITY INVOKER',
                converted_sql,
                flags=re.IGNORECASE,
            )
            changes.append(Change(
                rule_name="ADD_SQL_SECURITY_INVOKER",
                original="LANGUAGE SQL",
                replacement="LANGUAGE SQL\nSQL SECURITY INVOKER",
                confidence="high",
                note="SQL SECURITY INVOKER is required in Databricks procedures",
            ))

        status = ConversionStatus.NEEDS_REVIEW if result.escalate_to_ai else ConversionStatus.CONVERTED
        method = ConversionMethod.RULE_BASED

        # Try AI for complex SQL scripts
        if result.escalate_to_ai and ai_client is not None:
            try:
                ai_sql, tokens = ai_client.convert(
                    sql=converted_sql,
                    original_sql=sql,
                    object_type=ObjectType.PROCEDURE,
                    escalation_reasons=result.escalation_reasons,
                )
                converted_sql = ai_sql
                status = ConversionStatus.CONVERTED
                method = ConversionMethod.HYBRID
            except Exception:
                pass

        return ConversionResult(
            source_object=source_object,
            original_sql=sql,
            converted_sql=converted_sql,
            object_type=ObjectType.PROCEDURE,
            status=status,
            method=method,
            changes=changes,
        )
