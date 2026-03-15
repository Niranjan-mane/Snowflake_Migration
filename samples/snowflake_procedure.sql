-- Sample Snowflake stored procedures in multiple languages

-- 1. SQL Scripting procedure (Snowflake Scripting)
CREATE OR REPLACE PROCEDURE MYDB.PUBLIC.SP_UPDATE_CUSTOMER_TIER(
    MIN_LIFETIME_VALUE FLOAT,
    NEW_TIER VARCHAR
)
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
DECLARE
    affected_count INTEGER DEFAULT 0;
    result_msg VARCHAR;
BEGIN
    -- Update customers meeting the threshold
    UPDATE MYDB.PUBLIC.CUSTOMERS
    SET TIER = :NEW_TIER,
        UPDATED_AT = CURRENT_TIMESTAMP()
    WHERE LIFETIME_VALUE >= :MIN_LIFETIME_VALUE
      AND TIER != :NEW_TIER;

    -- Get count of affected rows
    LET affected_count := SQLROWCOUNT;

    -- Log the operation
    INSERT INTO MYDB.PUBLIC.AUDIT_LOG (operation, details, executed_at)
    VALUES (
        'UPDATE_TIER',
        'Set tier=' || :NEW_TIER || ' for ' || :affected_count || ' customers',
        CURRENT_TIMESTAMP()
    );

    LET result_msg := 'Updated ' || affected_count || ' customers to tier ' || NEW_TIER;
    RETURN result_msg;
EXCEPTION
    WHEN OTHER THEN
        RETURN 'Error: ' || SQLERRM;
END;
$$;


-- 2. JavaScript procedure
CREATE OR REPLACE PROCEDURE MYDB.PUBLIC.SP_GENERATE_REPORT(
    START_DATE VARCHAR,
    END_DATE VARCHAR
)
RETURNS VARIANT
LANGUAGE JAVASCRIPT
AS
$$
    // Build the report data
    var report = {
        generated_at: new Date().toISOString(),
        period: { start: START_DATE, end: END_DATE },
        metrics: {}
    };

    // Query total orders
    var orderQuery = snowflake.execute({
        sqlText: `SELECT COUNT(*) AS cnt, SUM(AMOUNT) AS total
                  FROM MYDB.PUBLIC.ORDERS
                  WHERE ORDER_DATE BETWEEN '${START_DATE}' AND '${END_DATE}'`
    });
    orderQuery.next();
    report.metrics.total_orders = orderQuery.getColumnValue('CNT');
    report.metrics.total_revenue = orderQuery.getColumnValue('TOTAL');

    // Query top products
    var productQuery = snowflake.execute({
        sqlText: `SELECT PRODUCT_ID, SUM(AMOUNT) AS revenue
                  FROM MYDB.PUBLIC.ORDERS
                  WHERE ORDER_DATE BETWEEN '${START_DATE}' AND '${END_DATE}'
                  GROUP BY PRODUCT_ID
                  ORDER BY revenue DESC
                  LIMIT 5`
    });

    var topProducts = [];
    while (productQuery.next()) {
        topProducts.push({
            product_id: productQuery.getColumnValue('PRODUCT_ID'),
            revenue: productQuery.getColumnValue('REVENUE')
        });
    }
    report.metrics.top_products = topProducts;

    return report;
$$;


-- 3. Python procedure (Snowpark)
CREATE OR REPLACE PROCEDURE MYDB.PUBLIC.SP_BULK_UPDATE_PRICES(
    CATEGORY VARCHAR,
    ADJUSTMENT_PCT FLOAT
)
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.10'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'run'
AS
$$
import snowflake.snowpark as snowpark
from snowflake.snowpark.functions import col, lit

def run(session: snowpark.Session, category: str, adjustment_pct: float) -> str:
    # Load products for the given category
    df = session.table("MYDB.PUBLIC.PRODUCTS").filter(col("CATEGORY") == category)

    # Apply price adjustment
    updated_df = df.with_column(
        "PRICE",
        col("PRICE") * lit(1 + adjustment_pct / 100)
    )

    # Save back
    updated_df.write.mode("overwrite").save_as_table("MYDB.PUBLIC.PRODUCTS")

    count = df.count()
    return f"Updated {count} products in category '{category}' by {adjustment_pct}%"
$$;
