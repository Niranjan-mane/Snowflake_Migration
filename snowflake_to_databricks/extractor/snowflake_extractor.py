"""
Extract all DDL objects from a Snowflake database/schema.
Uses GET_DDL() + INFORMATION_SCHEMA queries.
"""
from __future__ import annotations

import re
from typing import Optional

from snowflake_to_databricks.connectors.snowflake_connector import SnowflakeConnector
from snowflake_to_databricks.models import (
    ExtractionResult, ObjectType, ProcedureLanguage, SnowflakeObject,
)
from snowflake_to_databricks.parsers.sql_classifier import detect_procedure_language


class SnowflakeExtractor:

    def __init__(self, connector: SnowflakeConnector, config: dict):
        self._conn = connector
        self._cfg = config

    def extract_all(self, database: str, schema: str) -> ExtractionResult:
        result = ExtractionResult(database=database, schema=schema)
        object_types = self._cfg.get("object_types", [
            "tables", "views", "procedures", "functions", "sequences",
        ])

        extractors = {
            "tables":            self._extract_tables,
            "views":             self._extract_views,
            "materialized_views": self._extract_materialized_views,
            "procedures":        self._extract_procedures,
            "functions":         self._extract_functions,
            "sequences":         self._extract_sequences,
            "stages":            self._extract_stages,
            "streams":           self._extract_streams,
            "tasks":             self._extract_tasks,
        }

        for obj_type in object_types:
            fn = extractors.get(obj_type)
            if fn:
                try:
                    objects = fn(database, schema)
                    result.objects.extend(objects)
                except Exception as exc:
                    result.errors.append(f"Error extracting {obj_type}: {exc}")

        return result

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------

    def _extract_tables(self, db: str, schema: str) -> list[SnowflakeObject]:
        rows = self._conn.execute(f"""
            SELECT TABLE_NAME, ROW_COUNT, BYTES, COMMENT
            FROM {db}.INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = '{schema}'
              AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
        """)
        objects = []
        for row in rows:
            name = row.get("TABLE_NAME", "")
            ddl = self._get_ddl("TABLE", db, schema, name)
            if ddl:
                objects.append(SnowflakeObject(
                    name=name,
                    schema=schema,
                    database=db,
                    object_type=ObjectType.TABLE,
                    ddl=ddl,
                    row_count=row.get("ROW_COUNT"),
                    bytes_size=row.get("BYTES"),
                    comment=row.get("COMMENT"),
                ))
        return objects

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    def _extract_views(self, db: str, schema: str) -> list[SnowflakeObject]:
        rows = self._conn.execute(f"""
            SELECT TABLE_NAME, COMMENT
            FROM {db}.INFORMATION_SCHEMA.VIEWS
            WHERE TABLE_SCHEMA = '{schema}'
              AND TABLE_NAME NOT LIKE '%_MV'
            ORDER BY TABLE_NAME
        """)
        objects = []
        for row in rows:
            name = row.get("TABLE_NAME", "")
            ddl = self._get_ddl("VIEW", db, schema, name)
            if ddl:
                objects.append(SnowflakeObject(
                    name=name,
                    schema=schema,
                    database=db,
                    object_type=ObjectType.VIEW,
                    ddl=ddl,
                    comment=row.get("COMMENT"),
                ))
        return objects

    def _extract_materialized_views(self, db: str, schema: str) -> list[SnowflakeObject]:
        try:
            rows = self._conn.execute(f"SHOW MATERIALIZED VIEWS IN SCHEMA {db}.{schema}")
        except Exception:
            return []
        objects = []
        for row in rows:
            name = row.get("name", "")
            ddl = self._get_ddl("MATERIALIZED VIEW", db, schema, name)
            if ddl:
                objects.append(SnowflakeObject(
                    name=name,
                    schema=schema,
                    database=db,
                    object_type=ObjectType.MATERIALIZED_VIEW,
                    ddl=ddl,
                ))
        return objects

    # ------------------------------------------------------------------
    # Stored Procedures
    # ------------------------------------------------------------------

    def _extract_procedures(self, db: str, schema: str) -> list[SnowflakeObject]:
        rows = self._conn.execute(f"""
            SELECT PROCEDURE_NAME, ARGUMENT_SIGNATURE, PROCEDURE_LANGUAGE
            FROM {db}.INFORMATION_SCHEMA.PROCEDURES
            WHERE PROCEDURE_SCHEMA = '{schema}'
            ORDER BY PROCEDURE_NAME
        """)
        objects = []
        for row in rows:
            name = row.get("PROCEDURE_NAME", "")
            sig = row.get("ARGUMENT_SIGNATURE", "()")
            lang_str = (row.get("PROCEDURE_LANGUAGE") or "").upper()

            # Build fully qualified proc name for GET_DDL
            qualified = f"{db}.{schema}.{name}{sig}"
            ddl = self._get_ddl_raw(f"SELECT GET_DDL('PROCEDURE', '{qualified}')")

            if ddl:
                lang = self._map_language(lang_str)
                objects.append(SnowflakeObject(
                    name=name,
                    schema=schema,
                    database=db,
                    object_type=ObjectType.PROCEDURE,
                    ddl=ddl,
                    procedure_language=lang,
                ))
        return objects

    # ------------------------------------------------------------------
    # Functions
    # ------------------------------------------------------------------

    def _extract_functions(self, db: str, schema: str) -> list[SnowflakeObject]:
        rows = self._conn.execute(f"""
            SELECT FUNCTION_NAME, ARGUMENT_SIGNATURE, FUNCTION_LANGUAGE
            FROM {db}.INFORMATION_SCHEMA.FUNCTIONS
            WHERE FUNCTION_SCHEMA = '{schema}'
            ORDER BY FUNCTION_NAME
        """)
        objects = []
        for row in rows:
            name = row.get("FUNCTION_NAME", "")
            sig = row.get("ARGUMENT_SIGNATURE", "()")
            lang_str = (row.get("FUNCTION_LANGUAGE") or "").upper()
            qualified = f"{db}.{schema}.{name}{sig}"
            ddl = self._get_ddl_raw(f"SELECT GET_DDL('FUNCTION', '{qualified}')")
            if ddl:
                objects.append(SnowflakeObject(
                    name=name,
                    schema=schema,
                    database=db,
                    object_type=ObjectType.FUNCTION,
                    ddl=ddl,
                    procedure_language=self._map_language(lang_str),
                ))
        return objects

    # ------------------------------------------------------------------
    # Sequences
    # ------------------------------------------------------------------

    def _extract_sequences(self, db: str, schema: str) -> list[SnowflakeObject]:
        rows = self._conn.execute(f"""
            SELECT SEQUENCE_NAME, START_VALUE, INCREMENT, MINIMUM_VALUE, MAXIMUM_VALUE
            FROM {db}.INFORMATION_SCHEMA.SEQUENCES
            WHERE SEQUENCE_SCHEMA = '{schema}'
            ORDER BY SEQUENCE_NAME
        """)
        objects = []
        for row in rows:
            name = row.get("SEQUENCE_NAME", "")
            start = row.get("START_VALUE", 1)
            inc = row.get("INCREMENT", 1)
            ddl = f"CREATE SEQUENCE IF NOT EXISTS {name} START WITH {start} INCREMENT BY {inc};"
            objects.append(SnowflakeObject(
                name=name,
                schema=schema,
                database=db,
                object_type=ObjectType.SEQUENCE,
                ddl=ddl,
            ))
        return objects

    # ------------------------------------------------------------------
    # Stages, Streams, Tasks (SHOW commands)
    # ------------------------------------------------------------------

    def _extract_stages(self, db: str, schema: str) -> list[SnowflakeObject]:
        return self._extract_via_show("STAGES", db, schema, ObjectType.STAGE, "STAGE")

    def _extract_streams(self, db: str, schema: str) -> list[SnowflakeObject]:
        return self._extract_via_show("STREAMS", db, schema, ObjectType.STREAM, "STREAM")

    def _extract_tasks(self, db: str, schema: str) -> list[SnowflakeObject]:
        return self._extract_via_show("TASKS", db, schema, ObjectType.TASK, "TASK")

    def _extract_via_show(
        self, object_keyword: str, db: str, schema: str,
        obj_type: ObjectType, ddl_type: str,
    ) -> list[SnowflakeObject]:
        try:
            rows = self._conn.execute(f"SHOW {object_keyword} IN SCHEMA {db}.{schema}")
        except Exception:
            return []
        objects = []
        for row in rows:
            name = row.get("name", "")
            ddl = self._get_ddl(ddl_type, db, schema, name)
            if ddl:
                objects.append(SnowflakeObject(
                    name=name,
                    schema=schema,
                    database=db,
                    object_type=obj_type,
                    ddl=ddl,
                ))
        return objects

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_ddl(self, obj_type: str, db: str, schema: str, name: str) -> Optional[str]:
        qualified = f"{db}.{schema}.{name}"
        return self._get_ddl_raw(f"SELECT GET_DDL('{obj_type}', '{qualified}')")

    def _get_ddl_raw(self, sql: str) -> Optional[str]:
        try:
            result = self._conn.execute_scalar(sql)
            return str(result).strip() if result else None
        except Exception:
            return None

    @staticmethod
    def _map_language(lang_str: str) -> ProcedureLanguage:
        mapping = {
            "JAVASCRIPT": ProcedureLanguage.JAVASCRIPT,
            "PYTHON":     ProcedureLanguage.PYTHON,
            "JAVA":       ProcedureLanguage.JAVA,
            "SCALA":      ProcedureLanguage.SCALA,
            "SQL":        ProcedureLanguage.SQL,
        }
        return mapping.get(lang_str.upper(), ProcedureLanguage.UNKNOWN)
