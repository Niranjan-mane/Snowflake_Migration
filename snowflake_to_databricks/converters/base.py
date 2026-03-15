"""
Base converter: applies all rule sets and builds a ConversionResult.
"""
from __future__ import annotations

import re
from typing import Optional

from snowflake_to_databricks.models import (
    Change, ConversionMethod, ConversionResult, ConversionStatus,
    ObjectType, SnowflakeObject,
)
from snowflake_to_databricks.rules.rule_engine import RuleEngine, AppliedChange
from snowflake_to_databricks.rules.type_mappings import TYPE_RULES
from snowflake_to_databricks.rules.function_mappings import FUNCTION_RULES
from snowflake_to_databricks.rules.syntax_rules import SYNTAX_RULES
from snowflake_to_databricks.rules.ddl_rules import DDL_RULES

# Ordered rule sets — DDL rules first, then types, then functions, then syntax
ALL_RULES = DDL_RULES + TYPE_RULES + FUNCTION_RULES + SYNTAX_RULES

_SHARED_ENGINE = RuleEngine(ALL_RULES)


def _applied_to_change(a: AppliedChange) -> Change:
    return Change(
        rule_name=a.rule_name,
        original=a.original,
        replacement=a.replacement,
        line_number=a.line_number,
        confidence=a.confidence,
        note=a.note,
    )


class BaseConverter:
    """
    Applies all rule-based transformations to a SQL statement.
    Sub-classes override `_post_process` for type-specific adjustments.
    """

    def __init__(self, rule_engine: Optional[RuleEngine] = None):
        self._engine = rule_engine or _SHARED_ENGINE

    def convert(
        self,
        source_object: SnowflakeObject,
        ai_client=None,
    ) -> ConversionResult:
        sql = source_object.ddl
        result = self._engine.apply(sql)

        converted_sql = result.sql
        changes = [_applied_to_change(c) for c in result.changes]

        # Post-process (overridden by subclasses)
        converted_sql, extra_changes = self._post_process(converted_sql, source_object)
        changes.extend(extra_changes)

        # Determine method and status
        needs_ai = result.escalate_to_ai
        used_ai = False
        ai_tokens = 0
        status = ConversionStatus.CONVERTED

        if needs_ai:
            if ai_client is not None:
                try:
                    ai_sql, ai_tokens = ai_client.convert(
                        sql=converted_sql,
                        original_sql=sql,
                        object_type=source_object.object_type,
                        escalation_reasons=result.escalation_reasons,
                    )
                    converted_sql = ai_sql
                    used_ai = True
                    method = ConversionMethod.HYBRID
                except Exception as exc:
                    # AI failed — mark for review
                    converted_sql += (
                        f"\n-- MANUAL_REVIEW: AI conversion failed: {exc}\n"
                        f"-- Patterns needing review: {', '.join(result.escalation_reasons)}"
                    )
                    status = ConversionStatus.NEEDS_REVIEW
                    method = ConversionMethod.RULE_BASED
            else:
                # No AI client — add review comments
                for reason in result.escalation_reasons:
                    converted_sql += f"\n-- MANUAL_REVIEW: {reason}"
                status = ConversionStatus.NEEDS_REVIEW
                method = ConversionMethod.RULE_BASED
        else:
            method = ConversionMethod.RULE_BASED

        return ConversionResult(
            source_object=source_object,
            original_sql=sql,
            converted_sql=converted_sql,
            object_type=source_object.object_type,
            status=status,
            method=method,
            changes=changes,
            ai_used=used_ai,
            ai_tokens_used=ai_tokens,
        )

    def _post_process(
        self, sql: str, source_object: SnowflakeObject
    ) -> tuple[str, list[Change]]:
        """Override in subclasses for type-specific post-processing."""
        return sql, []
