"""
Databricks SQL connector — supports PAT, OAuth, and Azure AD authentication.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

try:
    from databricks import sql as dbsql
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False


class DatabricksConnector:
    """
    Manages a Databricks SQL Warehouse connection.

    Config keys (from config/databricks.yaml, env-interpolated):
      host        — workspace hostname  e.g. adb-xxx.azuredatabricks.net
      http_path   — SQL warehouse path  e.g. /sql/1.0/warehouses/xxx
      access_token — Personal Access Token (dapi...)
      auth_method  — pat | oauth | azure_ad
      catalog      — Unity Catalog name
      schema       — target schema
    """

    def __init__(self, config: dict):
        if not DB_AVAILABLE:
            raise ImportError(
                "databricks-sql-connector is not installed. "
                "Run: pip install databricks-sql-connector"
            )
        self._config = config
        self._conn = None

    def connect(self):
        """Open the Databricks SQL connection."""
        params = self._build_params()
        self._conn = dbsql.connect(**params)
        # Set catalog/schema context
        catalog = self._resolve(self._config.get("catalog", ""))
        schema = self._resolve(self._config.get("schema", ""))
        if catalog:
            self.execute(f"USE CATALOG `{catalog}`")
        if schema:
            self.execute(f"USE SCHEMA `{schema}`")
        return self

    def _build_params(self) -> dict:
        cfg = self._config
        params: dict[str, Any] = {
            "server_hostname": self._resolve(cfg["host"]),
            "http_path":       self._resolve(cfg["http_path"]),
            "http_timeout":    cfg.get("http_timeout", 120),
        }
        auth = cfg.get("auth_method", "pat").lower()
        if auth == "pat":
            params["access_token"] = self._resolve(cfg.get("access_token", ""))
        # oauth and azure_ad rely on environment / credential providers
        return {k: v for k, v in params.items() if v not in (None, "")}

    def execute(self, sql: str) -> list[tuple]:
        """Execute a SQL statement and return rows."""
        if self._conn is None:
            raise RuntimeError("Not connected. Call connect() first.")
        with self._conn.cursor() as cur:
            cur.execute(sql)
            try:
                return cur.fetchall()
            except Exception:
                return []

    def execute_ddl(self, sql: str) -> None:
        """Execute a DDL statement (no result expected)."""
        self.execute(sql)

    def explain(self, sql: str) -> bool:
        """
        Run EXPLAIN on a SQL statement to check for compilation errors.
        Returns True if SQL compiles successfully.
        """
        try:
            self.execute(f"EXPLAIN {sql}")
            return True
        except Exception:
            return False

    def table_exists(self, catalog: str, schema: str, table: str) -> bool:
        try:
            rows = self.execute(
                f"SHOW TABLES IN `{catalog}`.`{schema}` LIKE '{table}'"
            )
            return len(rows) > 0
        except Exception:
            return False

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def _resolve(value: str) -> str:
        if not isinstance(value, str):
            return value
        return re.sub(
            r'\$\{(\w+)\}',
            lambda m: os.getenv(m.group(1), m.group(0)),
            value,
        )
