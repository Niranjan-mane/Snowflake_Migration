"""
DML converter: SELECT, INSERT, UPDATE, DELETE, MERGE.
"""
from __future__ import annotations

import re
from typing import Optional

from snowflake_to_databricks.models import Change, SnowflakeObject
from snowflake_to_databricks.converters.base import BaseConverter
from snowflake_to_databricks.rules.rule_engine import RuleEngine

_TOP_N_RE = re.compile(r'/\*\s*TOP_(\d+)_MOVED_TO_LIMIT\s*\*/', re.IGNORECASE)


class DMLConverter(BaseConverter):

    def __init__(self, rule_engine: Optional[RuleEngine] = None):
        super().__init__(rule_engine)

    def _post_process(
        self, sql: str, source_object: SnowflakeObject
    ) -> tuple[str, list[Change]]:
        changes: list[Change] = []

        # Fix SELECT TOP N markers — move to LIMIT
        top_match = _TOP_N_RE.search(sql)
        if top_match:
            n = top_match.group(1)
            sql = _TOP_N_RE.sub('', sql).rstrip(';').rstrip()
            sql += f"\nLIMIT {n};"
            changes.append(Change(
                rule_name="SELECT_TOP_TO_LIMIT",
                original=f"SELECT TOP {n}",
                replacement=f"LIMIT {n}",
                confidence="high",
            ))

        return sql, changes
