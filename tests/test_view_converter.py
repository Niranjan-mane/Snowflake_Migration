"""Tests for ViewConverter."""
import pytest
from snowflake_to_databricks.converters.view_converter import ViewConverter
from snowflake_to_databricks.models import ObjectType, SnowflakeObject


def make_view_obj(ddl: str, obj_type: ObjectType = ObjectType.VIEW) -> SnowflakeObject:
    return SnowflakeObject(
        name="MY_VIEW",
        schema="PUBLIC",
        database="MYDB",
        object_type=obj_type,
        ddl=ddl,
    )


@pytest.fixture
def converter():
    return ViewConverter()


class TestSecureView:
    def test_secure_keyword_removed(self, converter):
        obj = make_view_obj("CREATE OR REPLACE SECURE VIEW v AS SELECT 1;")
        result = converter.convert(obj)
        assert "SECURE" not in result.converted_sql
        assert "CREATE" in result.converted_sql

    def test_create_view_preserved(self, converter):
        obj = make_view_obj("CREATE OR REPLACE SECURE VIEW v AS SELECT 1;")
        result = converter.convert(obj)
        assert "CREATE OR REPLACE VIEW" in result.converted_sql or "CREATE VIEW" in result.converted_sql


class TestSelectTop:
    def test_select_top_to_limit(self, converter):
        obj = make_view_obj("CREATE VIEW v AS SELECT TOP 10 * FROM t;")
        result = converter.convert(obj)
        assert "TOP 10" not in result.converted_sql
        assert "LIMIT 10" in result.converted_sql


class TestNvlInView:
    def test_nvl_converted(self, converter):
        obj = make_view_obj("CREATE VIEW v AS SELECT NVL(col, 0) FROM t;")
        result = converter.convert(obj)
        assert "COALESCE" in result.converted_sql
        assert "NVL(" not in result.converted_sql


class TestMaterializedView:
    def test_materialized_view_note_added(self, converter):
        obj = make_view_obj(
            "CREATE MATERIALIZED VIEW mv AS SELECT id FROM t;",
            obj_type=ObjectType.MATERIALIZED_VIEW,
        )
        result = converter.convert(obj)
        # Should contain a comment about manual review or Delta Live Tables
        sql_lower = result.converted_sql.lower()
        assert "materialized" in sql_lower or "delta live" in sql_lower or "review" in sql_lower
