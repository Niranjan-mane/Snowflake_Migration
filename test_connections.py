#!/usr/bin/env python3
"""
Connection test script.
Run this on your local machine first to verify credentials before the full migration.

    pip install -r requirements.txt
    python test_connections.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv()


def test_snowflake():
    print("\n" + "="*50)
    print("Testing Snowflake connection...")
    print("="*50)
    try:
        import snowflake.connector
        conn = snowflake.connector.connect(
            account=os.getenv("SF_ACCOUNT", "HCCFRGR-ZC29863"),
            user=os.getenv("SF_USER", "NIRANJAN"),
            password=os.getenv("SF_PASSWORD"),
            warehouse=os.getenv("SF_WAREHOUSE", "RISK_ANALYTICS_WH"),
            database=os.getenv("SF_DATABASE", "FRAUD_DETECTION"),
            schema=os.getenv("SF_SCHEMA", "PUBLIC"),
            login_timeout=30,
        )
        cur = conn.cursor()
        cur.execute("SELECT CURRENT_DATABASE(), CURRENT_SCHEMA(), CURRENT_WAREHOUSE(), CURRENT_USER()")
        db, schema, wh, user = cur.fetchone()
        print(f"  ✓ Connected as {user}")
        print(f"  ✓ Database : {db}")
        print(f"  ✓ Schema   : {schema}")
        print(f"  ✓ Warehouse: {wh}")

        # Count objects
        cur.execute(f"""
            SELECT TABLE_TYPE, COUNT(*) AS cnt
            FROM {db}.INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = '{schema}'
            GROUP BY TABLE_TYPE ORDER BY TABLE_TYPE
        """)
        rows = cur.fetchall()
        print(f"\n  Objects in {db}.{schema}:")
        for row in rows:
            print(f"    {row[0]:25s} : {row[1]}")

        cur.execute(f"""
            SELECT COUNT(*) FROM {db}.INFORMATION_SCHEMA.PROCEDURES
            WHERE PROCEDURE_SCHEMA = '{schema}'
        """)
        print(f"    {'PROCEDURES':25s} : {cur.fetchone()[0]}")

        cur.execute(f"""
            SELECT COUNT(*) FROM {db}.INFORMATION_SCHEMA.FUNCTIONS
            WHERE FUNCTION_SCHEMA = '{schema}'
        """)
        print(f"    {'FUNCTIONS':25s} : {cur.fetchone()[0]}")

        conn.close()
        print("\n  Snowflake: PASSED ✓")
        return True
    except Exception as e:
        print(f"\n  Snowflake: FAILED ✗\n  Error: {e}")
        return False


def test_databricks():
    print("\n" + "="*50)
    print("Testing Databricks connection...")
    print("="*50)
    try:
        from databricks import sql as dbsql
        conn = dbsql.connect(
            server_hostname=os.getenv("DB_HOST", "dbc-68d1085e-3edb.cloud.databricks.com"),
            http_path=os.getenv("DB_HTTP_PATH", "/sql/1.0/warehouses/f60638d87d534535"),
            access_token=os.getenv("DB_ACCESS_TOKEN"),
            http_timeout=60,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT current_user(), current_version()")
            user, version = cur.fetchone()
            print(f"  ✓ Connected as: {user}")
            print(f"  ✓ Databricks runtime: {version}")

            # Try creating catalog
            catalog = os.getenv("DB_CATALOG", "fraud_detection_db")
            schema = os.getenv("DB_SCHEMA", "public")
            try:
                cur.execute(f"CREATE CATALOG IF NOT EXISTS `{catalog}`")
                print(f"  ✓ Catalog '{catalog}' ready")
            except Exception as e:
                print(f"  ! Catalog creation: {e}")

            try:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{schema}`")
                print(f"  ✓ Schema '{catalog}.{schema}' ready")
            except Exception as e:
                print(f"  ! Schema creation: {e}")

        conn.close()
        print("\n  Databricks: PASSED ✓")
        return True
    except Exception as e:
        print(f"\n  Databricks: FAILED ✗\n  Error: {e}")
        return False


if __name__ == "__main__":
    sf_ok = test_snowflake()
    db_ok = test_databricks()

    print("\n" + "="*50)
    if sf_ok and db_ok:
        print("Both connections OK — ready to run migration:")
        print("  python run_migration.py")
    elif sf_ok:
        print("Snowflake OK but Databricks failed — check DB credentials")
        print("  python run_migration.py --extract-only   (extract to ./extracted/)")
    elif db_ok:
        print("Databricks OK but Snowflake failed — check SF credentials")
    else:
        print("Both connections failed — check your .env file")
    print("="*50)
    sys.exit(0 if (sf_ok and db_ok) else 1)
