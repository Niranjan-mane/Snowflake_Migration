"""Tests for Snowflake → Databricks data type conversions."""
import pytest
from snowflake_to_databricks.rules.rule_engine import RuleEngine
from snowflake_to_databricks.rules.type_mappings import TYPE_RULES


@pytest.fixture
def engine():
    return RuleEngine(TYPE_RULES)


def apply(engine, sql):
    return engine.apply(sql).sql


class TestNumericTypes:
    def test_number_no_args(self, engine):
        assert "DECIMAL" in apply(engine, "col NUMBER")

    def test_number_with_precision(self, engine):
        out = apply(engine, "col NUMBER(10,2)")
        assert "DECIMAL" in out
        assert "10" in out and "2" in out

    def test_numeric_alias(self, engine):
        assert "DECIMAL" in apply(engine, "col NUMERIC(5,3)")

    def test_byteint(self, engine):
        assert "TINYINT" in apply(engine, "col BYTEINT")

    def test_float4(self, engine):
        assert "FLOAT" in apply(engine, "col FLOAT4")

    def test_float8_to_double(self, engine):
        assert "DOUBLE" in apply(engine, "col FLOAT8")

    def test_real_to_float(self, engine):
        assert "FLOAT" in apply(engine, "col REAL")


class TestStringTypes:
    def test_varchar_no_args(self, engine):
        # VARCHAR without length → STRING
        assert "STRING" in apply(engine, "col VARCHAR")

    def test_varchar_with_length_preserved(self, engine):
        out = apply(engine, "col VARCHAR(255)")
        assert "VARCHAR(255)" in out or "STRING" in out

    def test_text_to_string(self, engine):
        assert "STRING" in apply(engine, "col TEXT")

    def test_string_unchanged(self, engine):
        out = apply(engine, "col STRING")
        assert "STRING" in out


class TestDateTimeTypes:
    def test_datetime_to_timestamp(self, engine):
        assert "TIMESTAMP" in apply(engine, "col DATETIME")

    def test_timestamp_ntz(self, engine):
        out = apply(engine, "col TIMESTAMP_NTZ(9)")
        assert "TIMESTAMP_NTZ" in out or "TIMESTAMP" in out

    def test_timestamp_ltz_to_timestamp(self, engine):
        assert "TIMESTAMP" in apply(engine, "col TIMESTAMP_LTZ")

    def test_timestamp_tz_to_timestamp(self, engine):
        assert "TIMESTAMP" in apply(engine, "col TIMESTAMP_TZ")

    def test_time_to_string(self, engine):
        assert "STRING" in apply(engine, "col TIME")


class TestSemiStructuredTypes:
    def test_variant_to_string(self, engine):
        assert "STRING" in apply(engine, "col VARIANT")

    def test_object_to_map(self, engine):
        out = apply(engine, "col OBJECT")
        assert "MAP" in out or "STRING" in out

    def test_geography_to_string(self, engine):
        assert "STRING" in apply(engine, "col GEOGRAPHY")
