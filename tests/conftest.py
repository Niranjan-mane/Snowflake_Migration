"""
Shared pytest fixtures for the migration agent test suite.
"""
import pytest
from snowflake_to_databricks.models import (
    ObjectType, ProcedureLanguage, SnowflakeObject,
)


def make_obj(ddl: str, name: str = "test_obj",
             obj_type: ObjectType = ObjectType.TABLE,
             proc_lang: ProcedureLanguage = None) -> SnowflakeObject:
    return SnowflakeObject(
        name=name,
        schema="PUBLIC",
        database="MYDB",
        object_type=obj_type,
        ddl=ddl,
        procedure_language=proc_lang,
    )


@pytest.fixture
def make_snowflake_obj():
    return make_obj
