"""
Deploy converted SQL objects to Databricks in dependency order.
"""
from __future__ import annotations

import time
from typing import Optional

from snowflake_to_databricks.connectors.databricks_connector import DatabricksConnector
from snowflake_to_databricks.models import (
    ConversionResult, ConversionStatus, DeployResult, DeployStatus, ObjectType,
)

# Deployment order by object type
_DEPLOY_ORDER = [
    ObjectType.SEQUENCE,
    ObjectType.TABLE,
    ObjectType.VIEW,
    ObjectType.MATERIALIZED_VIEW,
    ObjectType.FUNCTION,
    ObjectType.PROCEDURE,
    ObjectType.STREAM,
    ObjectType.STAGE,
    ObjectType.TASK,
    ObjectType.DML,
    ObjectType.UNKNOWN,
]


class DatabricksDeployer:

    def __init__(
        self,
        connector: DatabricksConnector,
        config: dict,
        dry_run: bool = False,
        stop_on_error: bool = False,
        use_explain: bool = True,
    ):
        self._conn = connector
        self._cfg = config
        self._dry_run = dry_run
        self._stop_on_error = stop_on_error
        self._use_explain = use_explain

    def deploy_all(
        self,
        results: list[ConversionResult],
    ) -> list[DeployResult]:
        """Deploy all converted objects in dependency order."""

        # Ensure catalog and schema exist
        self._ensure_catalog_schema()

        # Sort by deployment order
        ordered = sorted(
            results,
            key=lambda r: _DEPLOY_ORDER.index(r.object_type)
            if r.object_type in _DEPLOY_ORDER
            else len(_DEPLOY_ORDER),
        )

        deploy_results: list[DeployResult] = []
        for conv in ordered:
            result = self._deploy_one(conv)
            deploy_results.append(result)
            if self._stop_on_error and result.status == DeployStatus.FAILED:
                break

        return deploy_results

    def _deploy_one(self, conv: ConversionResult) -> DeployResult:
        name = conv.name
        obj_type = conv.object_type

        # Skip objects that need manual review
        if conv.status == ConversionStatus.NEEDS_REVIEW:
            return DeployResult(
                name=name,
                object_type=obj_type,
                status=DeployStatus.SKIPPED,
                ddl_executed="",
                error_message="Skipped: requires manual review before deployment",
            )

        sql = conv.converted_sql.strip()

        if self._dry_run:
            return DeployResult(
                name=name,
                object_type=obj_type,
                status=DeployStatus.DRY_RUN,
                ddl_executed=sql,
            )

        # EXPLAIN validation before deploy
        if self._use_explain and obj_type not in (ObjectType.PROCEDURE, ObjectType.STAGE, ObjectType.TASK):
            explain_sql = self._get_explain_sql(sql)
            if explain_sql and not self._conn.explain(explain_sql):
                return DeployResult(
                    name=name,
                    object_type=obj_type,
                    status=DeployStatus.FAILED,
                    ddl_executed=sql,
                    error_message="EXPLAIN validation failed — SQL would not compile",
                )

        # Execute
        start = time.time()
        try:
            for stmt in self._split_statements(sql):
                if stmt.strip():
                    self._conn.execute_ddl(stmt)
            elapsed_ms = int((time.time() - start) * 1000)
            return DeployResult(
                name=name,
                object_type=obj_type,
                status=DeployStatus.SUCCESS,
                ddl_executed=sql,
                execution_time_ms=elapsed_ms,
            )
        except Exception as exc:
            return DeployResult(
                name=name,
                object_type=obj_type,
                status=DeployStatus.FAILED,
                ddl_executed=sql,
                error_message=str(exc),
            )

    def _ensure_catalog_schema(self):
        deploy_cfg = self._cfg.get("deploy", {})
        catalog = self._cfg.get("catalog", "")
        schema = self._cfg.get("schema", "")

        if catalog and deploy_cfg.get("create_catalog_if_missing", True):
            try:
                self._conn.execute(f"CREATE CATALOG IF NOT EXISTS `{catalog}`")
            except Exception:
                pass

        if schema and deploy_cfg.get("create_schema_if_missing", True):
            try:
                catalog_prefix = f"`{catalog}`." if catalog else ""
                self._conn.execute(f"CREATE SCHEMA IF NOT EXISTS {catalog_prefix}`{schema}`")
            except Exception:
                pass

    @staticmethod
    def _get_explain_sql(sql: str) -> Optional[str]:
        """Extract a SELECT or DML statement suitable for EXPLAIN."""
        import re
        # Find first SELECT statement
        m = re.search(r'\bSELECT\b', sql, re.IGNORECASE)
        if m:
            return sql[m.start():]
        return None

    @staticmethod
    def _split_statements(sql: str) -> list[str]:
        """Split DDL that may contain multiple statements."""
        from snowflake_to_databricks.parsers.statement_splitter import split_statements
        return split_statements(sql)
