"""Tests for SQLValidator."""
import pytest
from snowflake_to_databricks.validators.sql_validator import SQLValidator


@pytest.fixture
def validator():
    return SQLValidator()


class TestCleanSQL:
    def test_simple_select_valid(self, validator):
        result = validator.validate("SELECT id, name FROM users WHERE id > 0")
        assert result.is_valid or len(result.errors) == 0

    def test_delta_table_valid(self, validator):
        result = validator.validate(
            "CREATE TABLE t (id BIGINT, name STRING) USING DELTA TBLPROPERTIES('delta.autoOptimize.optimizeWrite'='true')"
        )
        # Should not flag Delta-specific syntax as errors
        snowflake_errors = [e for e in result.errors if "snowflake" in e.message.lower()]
        assert len(snowflake_errors) == 0


class TestResidualPatterns:
    def test_nvl_flagged_as_error(self, validator):
        result = validator.validate("SELECT NVL(col, 0) FROM t")
        messages = [e.message for e in result.errors + result.warnings]
        assert any("NVL" in m for m in messages)

    def test_array_agg_flagged(self, validator):
        result = validator.validate("SELECT ARRAY_AGG(col) FROM t")
        messages = [e.message for e in result.errors + result.warnings]
        assert any("ARRAY_AGG" in m for m in messages)

    def test_object_construct_flagged(self, validator):
        result = validator.validate("SELECT OBJECT_CONSTRUCT('key', val) FROM t")
        messages = [e.message for e in result.errors + result.warnings]
        assert any("OBJECT_CONSTRUCT" in m or "NAMED_STRUCT" in m for m in messages)

    def test_dollar_sign_flagged(self, validator):
        result = validator.validate("SELECT $1, $2 FROM @stage")
        messages = [e.message for e in result.errors + result.warnings]
        assert any("$" in m or "stage" in m.lower() for m in messages)

    def test_lateral_flatten_flagged(self, validator):
        result = validator.validate(
            "SELECT f.value FROM t, LATERAL FLATTEN(input => t.arr) f"
        )
        messages = [e.message for e in result.errors + result.warnings]
        assert any("FLATTEN" in m for m in messages)


class TestWarningPatterns:
    def test_timestamp_tz_warning(self, validator):
        result = validator.validate("CREATE TABLE t (ts TIMESTAMP_TZ)")
        messages = [w.message for w in result.warnings]
        assert any("TIMESTAMP_TZ" in m for m in messages)

    def test_geography_warning(self, validator):
        result = validator.validate("CREATE TABLE t (loc GEOGRAPHY)")
        messages = [w.message for w in result.warnings]
        assert any("GEOGRAPHY" in m for m in messages)

    def test_manual_review_marker_warning(self, validator):
        result = validator.validate(
            "-- MANUAL_REVIEW: TASK requires Databricks Workflows\n"
            "CREATE TABLE t (id INT) USING DELTA"
        )
        messages = [w.message for w in result.warnings]
        assert any("MANUAL_REVIEW" in m or "review" in m.lower() for m in messages)


class TestValidationResult:
    def test_result_has_is_valid_field(self, validator):
        result = validator.validate("SELECT 1")
        assert hasattr(result, "is_valid")

    def test_result_has_errors_and_warnings(self, validator):
        result = validator.validate("SELECT 1")
        assert hasattr(result, "errors")
        assert hasattr(result, "warnings")
        assert isinstance(result.errors, list)
        assert isinstance(result.warnings, list)
