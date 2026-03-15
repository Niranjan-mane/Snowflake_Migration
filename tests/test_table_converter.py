"""Tests for TableConverter — Delta Lake DDL generation."""
import pytest
from snowflake_to_databricks.converters.table_converter import TableConverter
from snowflake_to_databricks.models import ObjectType, SnowflakeObject


def make_table_obj(ddl: str, name: str = "MY_TABLE") -> SnowflakeObject:
    return SnowflakeObject(
        name=name,
        schema="PUBLIC",
        database="MYDB",
        object_type=ObjectType.TABLE,
        ddl=ddl,
    )


@pytest.fixture
def converter():
    return TableConverter(add_auto_optimize=True, default_retention_days=7)


class TestDeltaLake:
    def test_using_delta_added(self, converter):
        obj = make_table_obj("CREATE TABLE t (id INT) ;")
        result = converter.convert(obj)
        assert "USING DELTA" in result.converted_sql

    def test_tblproperties_added(self, converter):
        obj = make_table_obj("CREATE TABLE t (id INT);")
        result = converter.convert(obj)
        assert "TBLPROPERTIES" in result.converted_sql

    def test_auto_optimize_properties(self, converter):
        obj = make_table_obj("CREATE TABLE t (id INT);")
        result = converter.convert(obj)
        sql = result.converted_sql
        assert "autoOptimize.optimizeWrite" in sql
        assert "autoOptimize.autoCompact" in sql

    def test_retention_property(self, converter):
        obj = make_table_obj("CREATE TABLE t (id INT);")
        result = converter.convert(obj)
        assert "deletedFileRetentionDuration" in result.converted_sql

    def test_no_duplicate_using_delta(self, converter):
        # Already has USING DELTA — should not duplicate
        obj = make_table_obj("CREATE TABLE t (id INT) USING DELTA TBLPROPERTIES();")
        result = converter.convert(obj)
        assert result.converted_sql.count("USING DELTA") == 1


class TestTransientTable:
    def test_transient_removed(self, converter):
        obj = make_table_obj("CREATE TRANSIENT TABLE t (id INT);")
        result = converter.convert(obj)
        assert "TRANSIENT" not in result.converted_sql
        assert "CREATE" in result.converted_sql


class TestAutoIncrement:
    def test_autoincrement_converted(self, converter):
        obj = make_table_obj("CREATE TABLE t (id INT AUTOINCREMENT);")
        result = converter.convert(obj)
        assert "AUTOINCREMENT" not in result.converted_sql
        assert "IDENTITY" in result.converted_sql or "GENERATED" in result.converted_sql

    def test_identity_converted(self, converter):
        obj = make_table_obj("CREATE TABLE t (id INT IDENTITY(1, 1));")
        result = converter.convert(obj)
        assert "IDENTITY(1, 1)" not in result.converted_sql or "GENERATED" in result.converted_sql


class TestDataRetention:
    def test_data_retention_converted_to_property(self, converter):
        obj = make_table_obj(
            "CREATE TABLE t (id INT) DATA_RETENTION_TIME_IN_DAYS = 14;"
        )
        result = converter.convert(obj)
        assert "DATA_RETENTION_TIME_IN_DAYS" not in result.converted_sql

    def test_copy_grants_removed(self, converter):
        obj = make_table_obj("CREATE TABLE t (id INT) COPY GRANTS;")
        result = converter.convert(obj)
        assert "COPY GRANTS" not in result.converted_sql

    def test_tag_handled(self, converter):
        obj = make_table_obj("CREATE TABLE t (id INT) TAG (env='prod');")
        result = converter.convert(obj)
        # TAG is either removed or replaced with a comment — either way USING DELTA is added
        assert "USING DELTA" in result.converted_sql


class TestClusterBy:
    def test_cluster_by_preserved(self, converter):
        obj = make_table_obj(
            "CREATE TABLE t (id INT, region STRING) CLUSTER BY (region);"
        )
        result = converter.convert(obj)
        assert "CLUSTER BY" in result.converted_sql
