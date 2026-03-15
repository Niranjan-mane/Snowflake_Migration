"""
Shared data models for the Snowflake → Databricks migration agent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ObjectType(str, Enum):
    TABLE = "table"
    VIEW = "view"
    MATERIALIZED_VIEW = "materialized_view"
    PROCEDURE = "procedure"
    FUNCTION = "function"
    SEQUENCE = "sequence"
    STAGE = "stage"
    STREAM = "stream"
    TASK = "task"
    DML = "dml"
    UNKNOWN = "unknown"


class ProcedureLanguage(str, Enum):
    JAVASCRIPT = "javascript"
    SQL = "sql"
    PYTHON = "python"
    JAVA = "java"
    SCALA = "scala"
    UNKNOWN = "unknown"


class ConversionMethod(str, Enum):
    RULE_BASED = "rule_based"
    AI = "ai"
    HYBRID = "hybrid"
    SQLGLOT = "sqlglot"
    MANUAL_REVIEW = "manual_review"


class ConversionStatus(str, Enum):
    CONVERTED = "converted"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    SKIPPED = "skipped"


class DeployStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


# ---------------------------------------------------------------------------
# Extraction models
# ---------------------------------------------------------------------------

@dataclass
class SnowflakeObject:
    """A single object extracted from Snowflake."""
    name: str
    schema: str
    database: str
    object_type: ObjectType
    ddl: str
    procedure_language: Optional[ProcedureLanguage] = None
    dependencies: list[str] = field(default_factory=list)
    row_count: Optional[int] = None
    bytes_size: Optional[int] = None
    comment: Optional[str] = None

    @property
    def qualified_name(self) -> str:
        return f"{self.database}.{self.schema}.{self.name}"


@dataclass
class ExtractionResult:
    """Result of extracting all objects from a Snowflake schema."""
    database: str
    schema: str
    objects: list[SnowflakeObject] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def by_type(self) -> dict[ObjectType, list[SnowflakeObject]]:
        result: dict[ObjectType, list[SnowflakeObject]] = {}
        for obj in self.objects:
            result.setdefault(obj.object_type, []).append(obj)
        return result

    @property
    def total_count(self) -> int:
        return len(self.objects)


# ---------------------------------------------------------------------------
# Conversion models
# ---------------------------------------------------------------------------

@dataclass
class Change:
    """A single rule-based transformation applied to SQL."""
    rule_name: str
    original: str
    replacement: str
    line_number: Optional[int] = None
    confidence: str = "high"      # high | medium | low
    note: str = ""


@dataclass
class ValidationIssue:
    """A validation issue found in converted SQL."""
    severity: Severity
    message: str
    line_number: Optional[int] = None
    pattern: Optional[str] = None


@dataclass
class ValidationResult:
    """Full validation result for a converted SQL statement."""
    is_valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    explain_passed: Optional[bool] = None

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]


@dataclass
class ConversionResult:
    """Full result of converting a single Snowflake SQL object."""
    source_object: SnowflakeObject
    original_sql: str
    converted_sql: str
    object_type: ObjectType
    status: ConversionStatus
    method: ConversionMethod
    changes: list[Change] = field(default_factory=list)
    validation: Optional[ValidationResult] = None
    ai_used: bool = False
    ai_tokens_used: int = 0
    error_message: Optional[str] = None

    @property
    def name(self) -> str:
        return self.source_object.name

    @property
    def change_count(self) -> int:
        return len(self.changes)


# ---------------------------------------------------------------------------
# Deployment models
# ---------------------------------------------------------------------------

@dataclass
class DeployResult:
    """Result of deploying a single converted object to Databricks."""
    name: str
    object_type: ObjectType
    status: DeployStatus
    ddl_executed: str = ""
    error_message: Optional[str] = None
    execution_time_ms: Optional[int] = None


# ---------------------------------------------------------------------------
# Migration report
# ---------------------------------------------------------------------------

@dataclass
class MigrationSummary:
    total_objects: int = 0
    converted: int = 0
    needs_review: int = 0
    failed: int = 0
    deployed: int = 0
    deploy_failed: int = 0
    ai_calls: int = 0
    total_ai_tokens: int = 0
    total_changes: int = 0
    validation_errors: int = 0
    validation_warnings: int = 0


@dataclass
class MigrationReport:
    """Full migration report produced at the end of a run."""
    summary: MigrationSummary
    conversion_results: list[ConversionResult] = field(default_factory=list)
    deploy_results: list[DeployResult] = field(default_factory=list)
    extraction_errors: list[str] = field(default_factory=list)
