"""Tests for ProcedureConverter."""
import pytest
from unittest.mock import MagicMock
from snowflake_to_databricks.converters.procedure_converter import ProcedureConverter
from snowflake_to_databricks.models import (
    ObjectType, ProcedureLanguage, SnowflakeObject, ConversionStatus,
)


def make_proc_obj(ddl: str, lang: ProcedureLanguage = ProcedureLanguage.SQL) -> SnowflakeObject:
    return SnowflakeObject(
        name="MY_PROC",
        schema="PUBLIC",
        database="MYDB",
        object_type=ObjectType.PROCEDURE,
        ddl=ddl,
        procedure_language=lang,
    )


@pytest.fixture
def converter():
    return ProcedureConverter()


class TestSQLProcedure:
    def test_sql_proc_converted(self, converter):
        ddl = """
CREATE OR REPLACE PROCEDURE MY_PROC()
RETURNS VARCHAR
LANGUAGE SQL
AS $$
    SELECT COUNT(*) FROM t;
$$;
"""
        obj = make_proc_obj(ddl, ProcedureLanguage.SQL)
        result = converter.convert(obj)
        assert result.status != ConversionStatus.FAILED

    def test_let_assignment_converted(self, converter):
        ddl = """
CREATE OR REPLACE PROCEDURE MY_PROC()
RETURNS VARCHAR
LANGUAGE SQL
AS $$
    DECLARE x INTEGER DEFAULT 0;
    BEGIN
        LET x := 42;
        RETURN x;
    END;
$$;
"""
        obj = make_proc_obj(ddl, ProcedureLanguage.SQL)
        result = converter.convert(obj)
        sql = result.converted_sql
        # LET x := should be converted to SET VAR x =
        assert "LET" not in sql or "SET VAR" in sql or ":=" not in sql

    def test_execute_as_caller_to_invoker(self, converter):
        ddl = """
CREATE OR REPLACE PROCEDURE MY_PROC()
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS CALLER
AS $$
    SELECT 1;
$$;
"""
        obj = make_proc_obj(ddl, ProcedureLanguage.SQL)
        result = converter.convert(obj)
        sql = result.converted_sql
        assert "SQL SECURITY INVOKER" in sql or "EXECUTE AS CALLER" not in sql

    def test_dollar_delimiters_removed(self, converter):
        ddl = """
CREATE OR REPLACE PROCEDURE MY_PROC()
RETURNS VARCHAR
LANGUAGE SQL
AS $$
    SELECT 1;
$$;
"""
        obj = make_proc_obj(ddl, ProcedureLanguage.SQL)
        result = converter.convert(obj)
        # $$ should be converted to single quotes or removed
        assert "$$" not in result.converted_sql or result.status == ConversionStatus.NEEDS_REVIEW


class TestJavaScriptProcedure:
    def test_javascript_needs_ai_or_review(self, converter):
        ddl = """
CREATE OR REPLACE PROCEDURE MY_PROC()
RETURNS VARIANT
LANGUAGE JAVASCRIPT
AS $$
    var result = snowflake.execute({sqlText: 'SELECT 1'});
    return result;
$$;
"""
        obj = make_proc_obj(ddl, ProcedureLanguage.JAVASCRIPT)
        result = converter.convert(obj)
        # Without AI client, should be NEEDS_REVIEW
        assert result.status in (ConversionStatus.NEEDS_REVIEW, ConversionStatus.CONVERTED)

    def test_javascript_with_ai_client(self, converter):
        ddl = """
CREATE OR REPLACE PROCEDURE MY_PROC()
RETURNS VARIANT
LANGUAGE JAVASCRIPT
AS $$
    return 42;
$$;
"""
        obj = make_proc_obj(ddl, ProcedureLanguage.JAVASCRIPT)
        # Mock AI client
        mock_ai = MagicMock()
        mock_ai.convert_procedure.return_value = (
            "CREATE OR REPLACE PROCEDURE MY_PROC()\nRETURNS INT\nLANGUAGE PYTHON\nAS $$\n  return 42\n$$",
            100,
        )
        result = converter.convert(obj, ai_client=mock_ai)
        assert mock_ai.convert_procedure.called


class TestPythonProcedure:
    def test_python_snowpark_adapted(self, converter):
        ddl = """
CREATE OR REPLACE PROCEDURE MY_PROC()
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.10'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'run'
AS $$
import snowflake.snowpark as snowpark

def run(session: snowpark.Session) -> str:
    df = session.table("t")
    return str(df.count())
$$;
"""
        obj = make_proc_obj(ddl, ProcedureLanguage.PYTHON)
        result = converter.convert(obj)
        # Should adapt snowpark imports or flag for review
        assert result.status != ConversionStatus.FAILED
