"""
Top-level migration agent — orchestrates the full pipeline:
  Extract → Convert → Validate → Deploy → Report
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

from snowflake_to_databricks.models import (
    ConversionResult, ConversionStatus, ExtractionResult,
    ObjectType, SnowflakeObject,
)
from snowflake_to_databricks.parsers.sql_classifier import classify, detect_procedure_language
from snowflake_to_databricks.parsers.statement_splitter import split_statements
from snowflake_to_databricks.converters.base import BaseConverter
from snowflake_to_databricks.converters.table_converter import TableConverter
from snowflake_to_databricks.converters.view_converter import ViewConverter
from snowflake_to_databricks.converters.dml_converter import DMLConverter
from snowflake_to_databricks.converters.procedure_converter import ProcedureConverter
from snowflake_to_databricks.converters.function_converter import FunctionConverter
from snowflake_to_databricks.validators.sql_validator import SQLValidator
from snowflake_to_databricks.reporter.report_generator import ReportGenerator


def _load_config(path: str) -> dict:
    load_dotenv()
    with open(path) as f:
        return yaml.safe_load(f)


def _resolve_env(value: str) -> str:
    import re
    if not isinstance(value, str):
        return value
    return re.sub(r'\$\{(\w+)\}', lambda m: os.getenv(m.group(1), m.group(0)), value)


class MigrationAgent:
    """
    Full Snowflake → Databricks migration orchestrator.
    """

    def __init__(self, config_dir: str = "config"):
        load_dotenv()
        self._config_dir = config_dir
        self._sf_config = self._load("snowflake.yaml").get("snowflake", {})
        self._db_config = self._load("databricks.yaml").get("databricks", {})
        self._conv_config = self._load("conversion.yaml").get("conversion", {})

        # AI client
        self._ai_client = None
        if self._conv_config.get("use_ai", False):
            api_key = _resolve_env(self._conv_config.get("anthropic_api_key", ""))
            if api_key and not api_key.startswith("${"):
                from snowflake_to_databricks.ai_converter.claude_client import ClaudeClient
                self._ai_client = ClaudeClient(api_key=api_key)

        # Converters
        delta_cfg = self._conv_config.get("delta_lake", {})
        self._converters = {
            ObjectType.TABLE:            TableConverter(
                add_auto_optimize=delta_cfg.get("add_auto_optimize", True),
                default_retention_days=delta_cfg.get("default_retention_days", 7),
            ),
            ObjectType.VIEW:             ViewConverter(),
            ObjectType.MATERIALIZED_VIEW: ViewConverter(),
            ObjectType.DML:              DMLConverter(),
            ObjectType.PROCEDURE:        ProcedureConverter(),
            ObjectType.FUNCTION:         FunctionConverter(),
            ObjectType.SEQUENCE:         BaseConverter(),
            ObjectType.STREAM:           BaseConverter(),
            ObjectType.STAGE:            BaseConverter(),
            ObjectType.TASK:             BaseConverter(),
            ObjectType.UNKNOWN:          BaseConverter(),
        }

        self._validator = SQLValidator()
        self._reporter = ReportGenerator(
            report_path=_resolve_env(self._conv_config.get("report_path", "./migration_report.json"))
        )

    # ------------------------------------------------------------------
    # Public pipeline methods
    # ------------------------------------------------------------------

    def extract(self, output_dir: str) -> ExtractionResult:
        """Connect to Snowflake and extract all object DDLs to files."""
        from snowflake_to_databricks.connectors.snowflake_connector import SnowflakeConnector
        from snowflake_to_databricks.extractor.snowflake_extractor import SnowflakeExtractor

        connector = SnowflakeConnector(self._sf_config)
        connector.connect()

        db = _resolve_env(self._sf_config.get("database", ""))
        schema = _resolve_env(self._sf_config.get("schema", ""))

        extractor = SnowflakeExtractor(connector, self._sf_config.get("extract", {}))
        result = extractor.extract_all(db, schema)
        connector.close()

        # Write to files
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        for obj in result.objects:
            fname = f"{obj.object_type.value}_{obj.name}.sql"
            (out / fname).write_text(obj.ddl, encoding="utf-8")

        return result

    def convert_directory(self, input_dir: str, output_dir: str) -> list[ConversionResult]:
        """Convert all .sql files in input_dir and write to output_dir."""
        in_path = Path(input_dir)
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        results: list[ConversionResult] = []
        for sql_file in sorted(in_path.glob("**/*.sql")):
            sql = sql_file.read_text(encoding="utf-8")
            file_results = self.convert_sql(sql, source_name=sql_file.stem)
            results.extend(file_results)

            # Write converted SQL
            for res in file_results:
                out_file = out_path / sql_file.relative_to(in_path)
                out_file.parent.mkdir(parents=True, exist_ok=True)
                out_file.write_text(res.converted_sql, encoding="utf-8")

        return results

    def convert_sql(self, sql: str, source_name: str = "inline") -> list[ConversionResult]:
        """Convert a SQL string (may contain multiple statements)."""
        statements = split_statements(sql)
        results: list[ConversionResult] = []

        for i, stmt in enumerate(statements):
            obj_type = classify(stmt)
            proc_lang = detect_procedure_language(stmt) if obj_type == ObjectType.PROCEDURE else None

            source_obj = SnowflakeObject(
                name=f"{source_name}_{i}" if len(statements) > 1 else source_name,
                schema="",
                database="",
                object_type=obj_type,
                ddl=stmt,
                procedure_language=proc_lang,
            )

            conv_result = self._convert_object(source_obj)
            # Validate
            conv_result.validation = self._validator.validate(conv_result.converted_sql)
            results.append(conv_result)

        return results

    def convert_objects(self, objects: list[SnowflakeObject]) -> list[ConversionResult]:
        """Convert a list of SnowflakeObject instances."""
        results = []
        for obj in objects:
            res = self._convert_object(obj)
            res.validation = self._validator.validate(res.converted_sql)
            results.append(res)
        return results

    def deploy(self, results: list[ConversionResult], dry_run: bool = False):
        """Deploy converted objects to Databricks."""
        from snowflake_to_databricks.connectors.databricks_connector import DatabricksConnector
        from snowflake_to_databricks.deployer.databricks_deployer import DatabricksDeployer

        connector = DatabricksConnector(self._db_config)
        connector.connect()

        deploy_cfg = self._db_config.get("deploy", {})
        deployer = DatabricksDeployer(
            connector=connector,
            config=self._db_config,
            dry_run=dry_run or deploy_cfg.get("dry_run", False),
            stop_on_error=deploy_cfg.get("stop_on_error", False),
            use_explain=deploy_cfg.get("use_explain_validation", True),
        )

        deploy_results = deployer.deploy_all(results)
        connector.close()
        return deploy_results

    def migrate(self, dry_run: bool = False):
        """Full pipeline: extract → convert → save files → deploy → report."""
        # Extract
        extract_dir = _resolve_env(self._sf_config.get("extract", {}).get("output_dir", "./extracted"))
        print(f"[1/5] Extracting from Snowflake → {extract_dir}")
        extraction = self.extract(extract_dir)
        print(f"      Extracted {extraction.total_count} objects")

        # Convert
        print(f"[2/5] Converting {extraction.total_count} objects...")
        conv_results = self.convert_objects(extraction.objects)

        # Save converted SQL to output_dir before deploying
        output_dir = _resolve_env(self._conv_config.get("output_dir", "./converted_sql"))
        print(f"[3/5] Saving converted SQL → {output_dir}")
        self._save_converted(conv_results, output_dir)
        print(f"      Saved {len(conv_results)} files to {output_dir}/")

        # Deploy
        print(f"[4/5] Deploying to Databricks (dry_run={dry_run})...")
        deploy_results = self.deploy(conv_results, dry_run=dry_run)

        # Report
        print(f"[5/5] Generating report...")
        report = self._reporter.build(conv_results, deploy_results, extraction.errors)
        self._reporter.save_json(report)
        self._reporter.print_summary(report)
        return report

    def _save_converted(self, results: list[ConversionResult], output_dir: str) -> None:
        """Write each converted SQL object to its own file in output_dir."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Also build a consolidated file for easy review
        consolidated_parts: list[str] = []

        for res in results:
            obj_type = res.object_type.value
            name = res.source_object.name
            fname = f"{obj_type}_{name}.sql"
            sql = res.converted_sql.strip()
            (out / fname).write_text(sql + "\n", encoding="utf-8")
            consolidated_parts.append(
                f"-- {'='*60}\n-- {obj_type.upper()}: {name}\n-- {'='*60}\n{sql}\n"
            )

        # Write consolidated file
        consolidated_path = out / "_all_objects.sql"
        consolidated_path.write_text("\n\n".join(consolidated_parts), encoding="utf-8")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _convert_object(self, obj: SnowflakeObject) -> ConversionResult:
        converter = self._converters.get(obj.object_type, self._converters[ObjectType.UNKNOWN])
        return converter.convert(obj, ai_client=self._ai_client)

    def _load(self, filename: str) -> dict:
        path = os.path.join(self._config_dir, filename)
        if not os.path.exists(path):
            return {}
        with open(path) as f:
            return yaml.safe_load(f) or {}
