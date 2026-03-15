#!/usr/bin/env python3
"""
Standalone migration runner.

Run this on a machine with network access to both Snowflake and Databricks:

    pip install -r requirements.txt
    python run_migration.py

Or for a dry-run (no deploy to Databricks):

    python run_migration.py --dry-run
"""
import sys
import os
import argparse

# ---------------------------------------------------------------------------
# Make sure the package is importable even without pip install
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from snowflake_to_databricks.agent import MigrationAgent


def main():
    parser = argparse.ArgumentParser(description="Snowflake → Databricks Migration Agent")
    parser.add_argument("--config-dir", default="config",
                        help="Directory containing snowflake.yaml, databricks.yaml, conversion.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="Convert and validate but do NOT deploy to Databricks")
    parser.add_argument("--extract-only", action="store_true",
                        help="Only extract DDL from Snowflake (saves to ./extracted/)")
    parser.add_argument("--convert-only", action="store_true",
                        help="Only convert ./extracted/ → ./converted/ (no extract, no deploy)")
    parser.add_argument("--deploy-only", action="store_true",
                        help="Only deploy ./converted/ to Databricks")
    args = parser.parse_args()

    agent = MigrationAgent(config_dir=args.config_dir)

    if args.extract_only:
        print("[*] Extracting from Snowflake...")
        result = agent.extract("./extracted")
        print(f"    Extracted {result.total_count} objects to ./extracted/")
        if result.errors:
            for e in result.errors:
                print(f"    [WARN] {e}")
        return

    if args.convert_only:
        print("[*] Converting ./extracted/ → ./converted/")
        results = agent.convert_directory("./extracted", "./converted")
        report = agent._reporter.build(results)
        agent._reporter.save_json(report)
        agent._reporter.print_summary(report)
        return

    if args.deploy_only:
        print("[*] Deploying ./converted/ to Databricks...")
        from snowflake_to_databricks.parsers.sql_classifier import classify, detect_procedure_language
        from snowflake_to_databricks.parsers.statement_splitter import split_statements
        from snowflake_to_databricks.models import (
            SnowflakeObject, ConversionResult, ConversionStatus,
            ConversionMethod, ObjectType,
        )
        from pathlib import Path
        results = []
        for f in sorted(Path("./converted").glob("**/*.sql")):
            sql = f.read_text(encoding="utf-8")
            for stmt in split_statements(sql):
                obj_type = classify(stmt)
                source = SnowflakeObject(name=f.stem, schema="", database="",
                                         object_type=obj_type, ddl=stmt)
                results.append(ConversionResult(
                    source_object=source, original_sql=stmt,
                    converted_sql=stmt, object_type=obj_type,
                    status=ConversionStatus.CONVERTED,
                    method=ConversionMethod.RULE_BASED,
                ))
        deploy_results = agent.deploy(results)
        ok = sum(1 for r in deploy_results if r.status.value == "success")
        fail = sum(1 for r in deploy_results if r.status.value == "failed")
        print(f"\nDeployed {ok} objects. Failed: {fail}")
        return

    # Full pipeline
    report = agent.migrate(dry_run=args.dry_run)
    sys.exit(0 if report.summary.failed == 0 else 1)


if __name__ == "__main__":
    main()
