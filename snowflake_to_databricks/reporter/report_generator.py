"""
Generate JSON + rich terminal migration report.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

from snowflake_to_databricks.models import (
    ConversionResult, ConversionStatus, DeployResult, DeployStatus,
    MigrationReport, MigrationSummary,
)

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


class ReportGenerator:

    def __init__(self, report_path: str = "./migration_report.json"):
        self._report_path = report_path
        self._console = Console() if RICH_AVAILABLE else None

    def build(
        self,
        conversion_results: list[ConversionResult],
        deploy_results: Optional[list[DeployResult]] = None,
        extraction_errors: Optional[list[str]] = None,
    ) -> MigrationReport:
        deploy_results = deploy_results or []
        extraction_errors = extraction_errors or []

        summary = MigrationSummary(
            total_objects=len(conversion_results),
            converted=sum(1 for r in conversion_results if r.status == ConversionStatus.CONVERTED),
            needs_review=sum(1 for r in conversion_results if r.status == ConversionStatus.NEEDS_REVIEW),
            failed=sum(1 for r in conversion_results if r.status == ConversionStatus.FAILED),
            deployed=sum(1 for r in deploy_results if r.status == DeployStatus.SUCCESS),
            deploy_failed=sum(1 for r in deploy_results if r.status == DeployStatus.FAILED),
            ai_calls=sum(1 for r in conversion_results if r.ai_used),
            total_ai_tokens=sum(r.ai_tokens_used for r in conversion_results),
            total_changes=sum(r.change_count for r in conversion_results),
            validation_errors=sum(
                len(r.validation.errors) for r in conversion_results
                if r.validation
            ),
            validation_warnings=sum(
                len(r.validation.warnings) for r in conversion_results
                if r.validation
            ),
        )

        return MigrationReport(
            summary=summary,
            conversion_results=conversion_results,
            deploy_results=deploy_results,
            extraction_errors=extraction_errors,
        )

    def save_json(self, report: MigrationReport) -> str:
        """Save the report as JSON and return the file path."""
        data = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "summary": {
                "total_objects":       report.summary.total_objects,
                "converted":           report.summary.converted,
                "needs_review":        report.summary.needs_review,
                "failed":              report.summary.failed,
                "deployed":            report.summary.deployed,
                "deploy_failed":       report.summary.deploy_failed,
                "ai_calls":            report.summary.ai_calls,
                "total_ai_tokens":     report.summary.total_ai_tokens,
                "total_changes":       report.summary.total_changes,
                "validation_errors":   report.summary.validation_errors,
                "validation_warnings": report.summary.validation_warnings,
            },
            "extraction_errors": report.extraction_errors,
            "objects": [
                self._conv_to_dict(r) for r in report.conversion_results
            ],
            "deployments": [
                self._deploy_to_dict(r) for r in report.deploy_results
            ],
        }

        os.makedirs(os.path.dirname(self._report_path) or ".", exist_ok=True)
        with open(self._report_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

        return self._report_path

    def print_summary(self, report: MigrationReport):
        """Print a rich summary table to the terminal."""
        if not RICH_AVAILABLE:
            self._print_plain(report)
            return

        console = self._console

        console.print("\n[bold cyan]═══ Migration Summary ═══[/bold cyan]")
        s = report.summary
        console.print(f"  Total objects  : [bold]{s.total_objects}[/bold]")
        console.print(f"  Converted      : [green]{s.converted}[/green]")
        console.print(f"  Needs review   : [yellow]{s.needs_review}[/yellow]")
        console.print(f"  Failed         : [red]{s.failed}[/red]")
        console.print(f"  Deployed       : [green]{s.deployed}[/green]")
        console.print(f"  Deploy failed  : [red]{s.deploy_failed}[/red]")
        console.print(f"  Total changes  : {s.total_changes}")
        console.print(f"  AI calls       : {s.ai_calls} ({s.total_ai_tokens:,} tokens)")
        console.print(f"  Validation ⚠   : {s.validation_warnings} warnings, {s.validation_errors} errors")

        # Per-object table
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold magenta")
        table.add_column("Object", style="cyan", no_wrap=True)
        table.add_column("Type")
        table.add_column("Status")
        table.add_column("Method")
        table.add_column("Changes")
        table.add_column("AI")
        table.add_column("Deploy")

        deploy_map = {r.name: r for r in report.deploy_results}

        for conv in report.conversion_results:
            deploy = deploy_map.get(conv.name)
            deploy_str = deploy.status.value if deploy else "-"
            deploy_color = {
                "success": "green", "failed": "red",
                "skipped": "yellow", "dry_run": "blue",
            }.get(deploy_str, "white")

            status_color = {
                "converted": "green", "needs_review": "yellow",
                "failed": "red", "skipped": "dim",
            }.get(conv.status.value, "white")

            table.add_row(
                conv.name[:40],
                conv.object_type.value,
                f"[{status_color}]{conv.status.value}[/{status_color}]",
                conv.method.value,
                str(conv.change_count),
                "✓" if conv.ai_used else "",
                f"[{deploy_color}]{deploy_str}[/{deploy_color}]",
            )

        console.print(table)
        console.print(f"\n[dim]Full report saved to: {self._report_path}[/dim]\n")

    def _print_plain(self, report: MigrationReport):
        s = report.summary
        print(f"\n=== Migration Summary ===")
        print(f"  Total: {s.total_objects} | Converted: {s.converted} | Review: {s.needs_review} | Failed: {s.failed}")
        print(f"  Deployed: {s.deployed} | Deploy failed: {s.deploy_failed}")
        print(f"  Report: {self._report_path}\n")

    @staticmethod
    def _conv_to_dict(r: ConversionResult) -> dict:
        return {
            "name":          r.name,
            "type":          r.object_type.value,
            "status":        r.status.value,
            "method":        r.method.value,
            "ai_used":       r.ai_used,
            "ai_tokens":     r.ai_tokens_used,
            "change_count":  r.change_count,
            "changes": [
                {
                    "rule":        c.rule_name,
                    "original":    c.original[:200],
                    "replacement": c.replacement[:200] if isinstance(c.replacement, str) else str(c.replacement)[:200],
                    "line":        c.line_number,
                    "confidence":  c.confidence,
                    "note":        c.note,
                }
                for c in r.changes
            ],
            "validation": {
                "is_valid": r.validation.is_valid if r.validation else None,
                "errors":   [{"msg": i.message, "line": i.line_number} for i in r.validation.errors] if r.validation else [],
                "warnings": [{"msg": i.message, "line": i.line_number} for i in r.validation.warnings] if r.validation else [],
            } if r.validation else None,
            "error_message": r.error_message,
        }

    @staticmethod
    def _deploy_to_dict(r: DeployResult) -> dict:
        return {
            "name":             r.name,
            "type":             r.object_type.value,
            "status":           r.status.value,
            "execution_time_ms": r.execution_time_ms,
            "error_message":    r.error_message,
        }
