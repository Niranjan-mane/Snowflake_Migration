from setuptools import setup, find_packages

setup(
    name="snowflake-to-databricks",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "snowflake-to-databricks=snowflake_to_databricks.cli.main:cli",
        ],
    },
)
