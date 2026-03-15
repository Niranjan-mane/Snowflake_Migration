"""
UDF / Function converter.
JavaScript UDFs → Python UDFs via AI.
SQL UDFs → rule-based.
"""
from __future__ import annotations

import re
from typing import Optional

from snowflake_to_databricks.models import (
    Change, ConversionMethod, ConversionResult, ConversionStatus,
    ObjectType, ProcedureLanguage, SnowflakeObject,
)
from snowflake_to_databricks.converters.base import BaseConverter
from snowflake_to_databricks.parsers.sql_classifier import detect_procedure_language
from snowflake_to_databricks.rules.rule_engine import RuleEngine

_RETURNS_TABLE_RE = re.compile(r'\bRETURNS\s+TABLE\b', re.IGNORECASE)


class FunctionConverter(BaseConverter):

    def __init__(self, rule_engine: Optional[RuleEngine] = None):
        super().__init__(rule_engine)

    def convert(
        self,
        source_object: SnowflakeObject,
        ai_client=None,
    ) -> ConversionResult:
        sql = source_object.ddl
        lang = detect_procedure_language(sql)

        if lang == ProcedureLanguage.JAVASCRIPT:
            if ai_client is not None:
                try:
                    converted_sql, tokens = ai_client.convert_procedure(
                        sql=sql, language="javascript_udf"
                    )
                    return ConversionResult(
                        source_object=source_object,
                        original_sql=sql,
                        converted_sql=converted_sql,
                        object_type=ObjectType.FUNCTION,
                        status=ConversionStatus.CONVERTED,
                        method=ConversionMethod.AI,
                        ai_used=True,
                        ai_tokens_used=tokens,
                    )
                except Exception as exc:
                    pass

            return ConversionResult(
                source_object=source_object,
                original_sql=sql,
                converted_sql=(
                    "-- MANUAL_REVIEW: JavaScript UDF requires conversion to Python UDF\n" + sql
                ),
                object_type=ObjectType.FUNCTION,
                status=ConversionStatus.NEEDS_REVIEW,
                method=ConversionMethod.MANUAL_REVIEW,
            )

        # SQL / Python UDFs — apply rule engine
        result = self._engine.apply(sql)
        changes_out = [
            Change(
                rule_name=c.rule_name,
                original=c.original,
                replacement=c.replacement,
                line_number=c.line_number,
                confidence=c.confidence,
                note=c.note,
            )
            for c in result.changes
        ]

        # Table-valued functions note
        converted_sql = result.sql
        if _RETURNS_TABLE_RE.search(converted_sql):
            converted_sql = (
                "-- NOTE: Table-valued functions require TABLE VALUED FUNCTION syntax in Databricks\n"
            ) + converted_sql

        status = ConversionStatus.NEEDS_REVIEW if result.escalate_to_ai else ConversionStatus.CONVERTED
        return ConversionResult(
            source_object=source_object,
            original_sql=sql,
            converted_sql=converted_sql,
            object_type=ObjectType.FUNCTION,
            status=status,
            method=ConversionMethod.RULE_BASED,
            changes=changes_out,
        )
