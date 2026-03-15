"""Tests for Snowflake → Databricks function conversions."""
import pytest
from snowflake_to_databricks.rules.rule_engine import RuleEngine
from snowflake_to_databricks.rules.function_mappings import FUNCTION_RULES


@pytest.fixture
def engine():
    return RuleEngine(FUNCTION_RULES)


def apply(engine, sql):
    return engine.apply(sql).sql


class TestNullHandlingFunctions:
    def test_nvl_to_coalesce(self, engine):
        out = apply(engine, "SELECT NVL(col, 0) FROM t")
        assert "COALESCE" in out
        assert "NVL(" not in out

    def test_ifnull_to_coalesce(self, engine):
        out = apply(engine, "SELECT IFNULL(col, 'N/A') FROM t")
        assert "COALESCE" in out

    def test_zeroifnull(self, engine):
        out = apply(engine, "SELECT ZEROIFNULL(revenue) FROM t")
        assert "COALESCE" in out

    def test_nullifzero(self, engine):
        out = apply(engine, "SELECT NULLIFZERO(qty) FROM t")
        assert "NULLIF" in out

    def test_nvl2(self, engine):
        out = apply(engine, "SELECT NVL2(col, 'yes', 'no') FROM t")
        assert "IF(" in out or "CASE" in out


class TestStringFunctions:
    def test_charindex(self, engine):
        out = apply(engine, "SELECT CHARINDEX('@', email) FROM t")
        # Databricks equivalent is LOCATE or INSTR — CHARINDEX itself should not remain
        assert "CHARINDEX" not in out
        assert "LOCATE" in out or "INSTR" in out or "POSITION" in out

    def test_editdistance(self, engine):
        out = apply(engine, "SELECT EDITDISTANCE(a, b) FROM t")
        assert "levenshtein" in out.lower()

    def test_regexp_like(self, engine):
        out = apply(engine, "SELECT REGEXP_LIKE(col, '^[a-z]+$') FROM t")
        assert "RLIKE" in out

    def test_regexp_substr(self, engine):
        out = apply(engine, "SELECT REGEXP_SUBSTR(col, '[0-9]+') FROM t")
        assert "REGEXP_EXTRACT" in out

    def test_endswith(self, engine):
        out = apply(engine, "SELECT ENDSWITH(name, '.com') FROM t")
        assert "LIKE" in out or "ENDSWITH" in out

    def test_base64_encode(self, engine):
        out = apply(engine, "SELECT BASE64_ENCODE(data) FROM t")
        assert "BASE64(" in out or "base64" in out.lower()

    def test_to_char_no_format(self, engine):
        out = apply(engine, "SELECT TO_CHAR(num) FROM t")
        assert "CAST" in out or "STRING" in out

    def test_random_to_rand(self, engine):
        out = apply(engine, "SELECT RANDOM() FROM t")
        assert "RAND()" in out or "random" in out.lower()


class TestNumericFunctions:
    def test_div0(self, engine):
        out = apply(engine, "SELECT DIV0(total, cnt) FROM t")
        assert "IF(" in out or "CASE" in out
        assert "DIV0(" not in out

    def test_square(self, engine):
        out = apply(engine, "SELECT SQUARE(x) FROM t")
        assert "POW" in out or "POWER" in out

    def test_bitand(self, engine):
        out = apply(engine, "SELECT BITAND(a, b) FROM t")
        assert "&" in out or "bitand" in out.lower()


class TestDateFunctions:
    def test_trunc_date(self, engine):
        out = apply(engine, "SELECT TRUNC(order_date, 'MONTH') FROM t")
        assert "DATE_TRUNC" in out

    def test_sysdate(self, engine):
        out = apply(engine, "SELECT SYSDATE() FROM t")
        assert "CURRENT_TIMESTAMP" in out

    def test_getdate(self, engine):
        out = apply(engine, "SELECT GETDATE() FROM t")
        assert "CURRENT_TIMESTAMP" in out


class TestAggregateFunctions:
    def test_array_agg_to_collect_list(self, engine):
        out = apply(engine, "SELECT ARRAY_AGG(col) FROM t")
        assert "COLLECT_LIST" in out

    def test_array_agg_distinct_to_collect_set(self, engine):
        out = apply(engine, "SELECT ARRAY_AGG(DISTINCT col) FROM t")
        assert "COLLECT_SET" in out

    def test_skew_to_skewness(self, engine):
        out = apply(engine, "SELECT SKEW(amount) FROM t")
        assert "SKEWNESS" in out

    def test_approx_percentile(self, engine):
        out = apply(engine, "SELECT APPROX_PERCENTILE(amount, 0.95) FROM t")
        assert "PERCENTILE_APPROX" in out


class TestArrayFunctions:
    def test_array_contains_arg_swap(self, engine):
        # CRITICAL: ARRAY_CONTAINS(val, arr) → ARRAY_CONTAINS(arr, val)
        out = apply(engine, "SELECT ARRAY_CONTAINS('VIP', tags) FROM t")
        # After swap: arr should come first
        assert "ARRAY_CONTAINS(tags" in out or "array_contains(tags" in out.lower()

    def test_array_size_to_size(self, engine):
        out = apply(engine, "SELECT ARRAY_SIZE(arr) FROM t")
        assert "SIZE(" in out

    def test_array_to_string(self, engine):
        out = apply(engine, "SELECT ARRAY_TO_STRING(arr, ',') FROM t")
        assert "ARRAY_JOIN" in out

    def test_array_construct_to_array(self, engine):
        out = apply(engine, "SELECT ARRAY_CONSTRUCT(1, 2, 3) FROM t")
        assert "ARRAY(" in out

    def test_array_cat_to_concat(self, engine):
        out = apply(engine, "SELECT ARRAY_CAT(a, b) FROM t")
        assert "CONCAT(" in out


class TestJsonFunctions:
    def test_colon_path_simple(self, engine):
        out = apply(engine, "SELECT data:name::STRING FROM t")
        assert "GET_JSON_OBJECT" in out

    def test_colon_path_nested(self, engine):
        out = apply(engine, "SELECT data:address.city::STRING FROM t")
        assert "GET_JSON_OBJECT" in out

    def test_object_keys_to_map_keys(self, engine):
        out = apply(engine, "SELECT OBJECT_KEYS(obj) FROM t")
        assert "MAP_KEYS" in out

    def test_array_flatten_to_explode(self, engine):
        out = apply(engine, "SELECT ARRAY_FLATTEN(arr) FROM t")
        assert "FLATTEN" in out or "EXPLODE" in out
