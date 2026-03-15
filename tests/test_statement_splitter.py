"""Tests for the SQL statement splitter."""
import pytest
from snowflake_to_databricks.parsers.statement_splitter import split_statements


class TestSingleStatement:
    def test_simple_select(self):
        stmts = split_statements("SELECT 1;")
        assert len(stmts) == 1
        assert "SELECT 1" in stmts[0]

    def test_no_trailing_semicolon(self):
        stmts = split_statements("SELECT 1")
        assert len(stmts) == 1

    def test_whitespace_only(self):
        stmts = split_statements("   \n\n  ")
        assert len(stmts) == 0

    def test_empty_string(self):
        stmts = split_statements("")
        assert len(stmts) == 0


class TestMultipleStatements:
    def test_two_selects(self):
        stmts = split_statements("SELECT 1; SELECT 2;")
        assert len(stmts) == 2

    def test_three_statements(self):
        sql = "CREATE TABLE t (id INT);\nINSERT INTO t VALUES (1);\nSELECT * FROM t;"
        stmts = split_statements(sql)
        assert len(stmts) == 3


class TestDollarQuoting:
    def test_dollar_dollar_not_split(self):
        sql = """
CREATE PROCEDURE p()
RETURNS VARCHAR
AS $$
    BEGIN
        SELECT 1; SELECT 2;
    END;
$$;
"""
        stmts = split_statements(sql)
        # Semicolons inside $$ should not split
        assert len(stmts) == 1

    def test_after_dollar_quote(self):
        sql = """
CREATE PROCEDURE p() AS $$
    SELECT 1;
$$;
SELECT 2;
"""
        stmts = split_statements(sql)
        assert len(stmts) == 2


class TestQuotedStrings:
    def test_semicolon_in_string(self):
        stmts = split_statements("SELECT 'hello; world' AS s;")
        assert len(stmts) == 1

    def test_semicolon_in_double_quoted(self):
        stmts = split_statements('SELECT "col;name" FROM t;')
        assert len(stmts) == 1


class TestComments:
    def test_line_comment_ignored(self):
        sql = "-- This is a comment; not a statement\nSELECT 1;"
        stmts = split_statements(sql)
        assert len(stmts) == 1
        assert "SELECT 1" in stmts[0]

    def test_block_comment_ignored(self):
        sql = "/* comment; with semicolon */\nSELECT 1;"
        stmts = split_statements(sql)
        assert len(stmts) == 1
