"""
CLI entry point for the Snowflake → Databricks migration agent.

Commands:
  migrate   — Full pipeline (extract → convert → deploy → report)
  extract   — Extract objects from Snowflake to SQL files
  convert   — Convert extracted SQL files to Databricks SQL
  validate  — Validate SQL files using sqlglot
  deploy    — Deploy converted SQL to Databricks
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from snowflake_to_databricks.agent import MigrationAgent
from snowflake_to_databricks.validators.sql_validator import SQLValidator


@click.group()
@click.version_option(package_name="snowflake-to-databricks", prog_name="snowflake-to-databricks")
def cli():
    """Snowflake → Databricks migration agent."""


# ---------------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--config-dir", default="config", show_default=True,
              help="Directory containing snowflake.yaml, databricks.yaml, conversion.yaml")
@click.option("--dry-run", is_flag=True, default=False,
              help="Convert and validate, but do not deploy to Databricks")
def migrate(config_dir: str, dry_run: bool):
    """
    Full pipeline: extract → convert → validate → deploy → report.

    Reads all config from CONFIG_DIR (default: ./config/).
    """
    try:
        agent = MigrationAgent(config_dir=config_dir)
        report = agent.migrate(dry_run=dry_run)
        sys.exit(0 if report.summary.failed == 0 else 1)
    except Exception as exc:
        click.echo(f"[ERROR] Migration failed: {exc}", err=True)
        sys.exit(2)


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--config-dir", default="config", show_default=True,
              help="Directory containing snowflake.yaml")
@click.option("--output-dir", default="./extracted", show_default=True,
              help="Directory to write extracted SQL files")
def extract(config_dir: str, output_dir: str):
    """
    Connect to Snowflake and extract all object DDLs to SQL files.
    """
    try:
        agent = MigrationAgent(config_dir=config_dir)
        result = agent.extract(output_dir)
        click.echo(f"Extracted {result.total_count} objects to {output_dir}")
        if result.errors:
            for err in result.errors:
                click.echo(f"  [WARN] {err}", err=True)
        sys.exit(0)
    except Exception as exc:
        click.echo(f"[ERROR] Extraction failed: {exc}", err=True)
        sys.exit(2)


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--config-dir", default="config", show_default=True,
              help="Directory containing conversion.yaml (for AI settings)")
@click.option("--input-dir", required=True,
              help="Directory containing extracted Snowflake SQL files")
@click.option("--output-dir", required=True,
              help="Directory to write converted Databricks SQL files")
@click.option("--report", default="./migration_report.json", show_default=True,
              help="Path for JSON migration report")
def convert(config_dir: str, input_dir: str, output_dir: str, report: str):
    """
    Convert all Snowflake SQL files in INPUT_DIR to Databricks SQL.
    Writes converted files to OUTPUT_DIR and generates a JSON report.
    """
    try:
        agent = MigrationAgent(config_dir=config_dir)
        results = agent.convert_directory(input_dir, output_dir)

        # Build and save report
        migration_report = agent._reporter.build(results)
        agent._reporter.save_json(migration_report)
        agent._reporter.print_summary(migration_report)

        failed = sum(1 for r in results if r.status.value == "failed")
        sys.exit(0 if failed == 0 else 1)
    except Exception as exc:
        click.echo(f"[ERROR] Conversion failed: {exc}", err=True)
        sys.exit(2)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--input-dir", required=True,
              help="Directory containing converted SQL files to validate")
@click.option("--strict", is_flag=True, default=False,
              help="Exit non-zero if any warnings are found")
def validate(input_dir: str, strict: bool):
    """
    Validate converted Databricks SQL files using sqlglot.
    Reports errors and warnings without deploying.
    """
    validator = SQLValidator()
    total = errors = warnings = 0

    for sql_file in sorted(Path(input_dir).glob("**/*.sql")):
        sql = sql_file.read_text(encoding="utf-8")
        result = validator.validate(sql)
        total += 1

        if result.errors or result.warnings:
            click.echo(f"\n{sql_file.name}:")
            for err in result.errors:
                click.echo(f"  [ERROR] line {err.line_number}: {err.message}")
                errors += 1
            for warn in result.warnings:
                click.echo(f"  [WARN]  line {warn.line_number}: {warn.message}")
                warnings += 1

    click.echo(f"\nValidated {total} files — {errors} errors, {warnings} warnings")
    if errors > 0:
        sys.exit(1)
    if strict and warnings > 0:
        sys.exit(1)
    sys.exit(0)


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--config-dir", default="config", show_default=True,
              help="Directory containing databricks.yaml")
@click.option("--input-dir", required=True,
              help="Directory containing converted Databricks SQL files")
@click.option("--dry-run", is_flag=True, default=False,
              help="Show SQL that would be executed without actually deploying")
@click.option("--stop-on-error", is_flag=True, default=False,
              help="Stop deployment on first error")
def deploy(config_dir: str, input_dir: str, dry_run: bool, stop_on_error: bool):
    """
    Deploy converted SQL files to Databricks.

    Connects using config/databricks.yaml settings.
    Objects are deployed in dependency order:
    Sequences → Tables → Views → Functions → Procedures → DML
    """
    try:
        from snowflake_to_databricks.connectors.databricks_connector import DatabricksConnector
        from snowflake_to_databricks.deployer.databricks_deployer import DatabricksDeployer
        from snowflake_to_databricks.parsers.sql_classifier import classify, detect_procedure_language
        from snowflake_to_databricks.parsers.statement_splitter import split_statements
        from snowflake_to_databricks.models import (
            SnowflakeObject, ConversionResult, ConversionStatus,
            ConversionMethod, ObjectType,
        )
        import yaml, os, re

        # Load config
        db_config_path = os.path.join(config_dir, "databricks.yaml")
        if not os.path.exists(db_config_path):
            click.echo(f"[ERROR] Config not found: {db_config_path}", err=True)
            sys.exit(2)

        def _resolve(value):
            if not isinstance(value, str):
                return value
            return re.sub(r'\$\{(\w+)\}', lambda m: os.getenv(m.group(1), m.group(0)), value)

        with open(db_config_path) as f:
            db_config = yaml.safe_load(f).get("databricks", {})

        # Load SQL files as ConversionResult objects
        results = []
        for sql_file in sorted(Path(input_dir).glob("**/*.sql")):
            sql = sql_file.read_text(encoding="utf-8")
            statements = split_statements(sql)
            for stmt in statements:
                obj_type = classify(stmt)
                proc_lang = detect_procedure_language(stmt) if obj_type == ObjectType.PROCEDURE else None
                name = sql_file.stem

                source = SnowflakeObject(
                    name=name, schema="", database="",
                    object_type=obj_type, ddl=stmt,
                    procedure_language=proc_lang,
                )
                results.append(ConversionResult(
                    source_object=source,
                    original_sql=stmt,
                    converted_sql=stmt,
                    object_type=obj_type,
                    status=ConversionStatus.CONVERTED,
                    method=ConversionMethod.RULE_BASED,
                ))

        connector = DatabricksConnector(db_config)
        connector.connect()

        deployer = DatabricksDeployer(
            connector=connector,
            config=db_config,
            dry_run=dry_run,
            stop_on_error=stop_on_error,
        )
        deploy_results = deployer.deploy_all(results)
        connector.close()

        succeeded = sum(1 for r in deploy_results if r.status.value == "success")
        failed = sum(1 for r in deploy_results if r.status.value == "failed")
        click.echo(f"\nDeployed {succeeded} objects. Failed: {failed}")
        sys.exit(0 if failed == 0 else 1)

    except Exception as exc:
        click.echo(f"[ERROR] Deploy failed: {exc}", err=True)
        sys.exit(2)


if __name__ == "__main__":
    cli()
