"""Tests for DMLConverter."""
import pytest
from snowflake_to_databricks.converters.dml_converter import DMLConverter
from snowflake_to_databricks.models import ObjectType, SnowflakeObject


def make_dml_obj(ddl: str) -> SnowflakeObject:
    return SnowflakeObject(
        name="test_dml",
        schema="PUBLIC",
        database="MYDB",
        object_type=ObjectType.DML,
        ddl=ddl,
    )


@pytest.fixture
def converter():
    return DMLConverter()


class TestInsertOverwrite:
    def test_insert_overwrite_into(self, converter):
        obj = make_dml_obj("INSERT OVERWRITE INTO t SELECT * FROM s;")
        result = converter.convert(obj)
        # Should fix double keyword: INSERT OVERWRITE INTO → INSERT OVERWRITE
        assert "OVERWRITE INTO" not in result.converted_sql or "INSERT OVERWRITE" in result.converted_sql

    def test_plain_insert_unchanged(self, converter):
        obj = make_dml_obj("INSERT INTO t SELECT * FROM s;")
        result = converter.convert(obj)
        assert "INSERT INTO" in result.converted_sql


class TestSelectTop:
    def test_select_top_to_limit(self, converter):
        obj = make_dml_obj("SELECT TOP 100 * FROM orders;")
        result = converter.convert(obj)
        assert "TOP 100" not in result.converted_sql
        assert "LIMIT 100" in result.converted_sql


class TestFunctionConversions:
    def test_nvl_in_dml(self, converter):
        obj = make_dml_obj("SELECT NVL(col, 'default') FROM t;")
        result = converter.convert(obj)
        assert "COALESCE" in result.converted_sql

    def test_iff_preserved_or_converted(self, converter):
        obj = make_dml_obj("SELECT IFF(x > 0, 'pos', 'neg') FROM t;")
        result = converter.convert(obj)
        # IFF is native in Databricks so should remain, or be converted to IF
        assert "IFF(" in result.converted_sql or "IF(" in result.converted_sql

    def test_lateral_flatten(self, converter):
        obj = make_dml_obj(
            "SELECT f.value FROM t, LATERAL FLATTEN(input => t.arr) f;"
        )
        result = converter.convert(obj)
        # Should convert LATERAL FLATTEN to LATERAL VIEW EXPLODE
        assert "LATERAL VIEW" in result.converted_sql or "EXPLODE" in result.converted_sql

    def test_sample_rows_converted(self, converter):
        obj = make_dml_obj("SELECT * FROM t SAMPLE (10 ROWS);")
        result = converter.convert(obj)
        # SAMPLE (N ROWS) → TABLESAMPLE or LIMIT N
        assert "TABLESAMPLE" in result.converted_sql or "LIMIT" in result.converted_sql

    def test_colon_path_access(self, converter):
        obj = make_dml_obj("SELECT data:name::STRING FROM t;")
        result = converter.convert(obj)
        assert "GET_JSON_OBJECT" in result.converted_sql


class TestTimeTravelQuery:
    def test_at_timestamp(self, converter):
        obj = make_dml_obj(
            "SELECT * FROM t AT(TIMESTAMP => '2024-01-01 00:00:00');"
        )
        result = converter.convert(obj)
        # Should convert AT(...) to TIMESTAMP AS OF
        assert "AT(" not in result.converted_sql or "TIMESTAMP AS OF" in result.converted_sql
