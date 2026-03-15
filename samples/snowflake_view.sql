-- Sample Snowflake views demonstrating function conversions

CREATE OR REPLACE SECURE VIEW MYDB.PUBLIC.VW_CUSTOMER_SUMMARY AS
SELECT
    c.CUSTOMER_ID,
    c.CUSTOMER_NAME,
    NVL(c.EMAIL, 'no-email@unknown.com')          AS EMAIL,
    NVL2(c.PHONE, 'has_phone', 'no_phone')        AS PHONE_STATUS,
    ZEROIFNULL(c.LIFETIME_VALUE)                   AS LIFETIME_VALUE,
    IFF(c.IS_ACTIVE, 'Active', 'Inactive')        AS STATUS,
    DECODE(c.TIER,
        'GOLD',     'Premium',
        'SILVER',   'Standard',
        NULL,       'Unknown',
                    'Basic')                       AS TIER_LABEL,
    TRUNC(c.SIGNUP_DATE, 'MONTH')                 AS SIGNUP_MONTH,
    DATEDIFF('day', c.SIGNUP_DATE, CURRENT_DATE())  AS DAYS_SINCE_SIGNUP,
    DATEADD('month', 3, c.SIGNUP_DATE)             AS TRIAL_END_DATE,
    TO_CHAR(c.SIGNUP_DATE, 'YYYY-MM-DD')           AS SIGNUP_DATE_STR,
    REGEXP_LIKE(c.EMAIL, '^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$') AS IS_VALID_EMAIL,
    CHARINDEX('@', c.EMAIL)                         AS AT_POSITION,
    c.PROFILE_DATA:first_name::STRING              AS FIRST_NAME,
    c.PROFILE_DATA:address.city::STRING            AS CITY,
    ARRAY_SIZE(c.ORDER_HISTORY)                    AS ORDER_COUNT,
    ARRAY_CONTAINS('VIP'::VARIANT, c.TAGS)         AS IS_VIP
FROM MYDB.PUBLIC.CUSTOMERS c
WHERE c.CREATED_AT >= DATEADD('year', -2, CURRENT_TIMESTAMP())
QUALIFY ROW_NUMBER() OVER (PARTITION BY c.CUSTOMER_ID ORDER BY c.UPDATED_AT DESC) = 1;


-- View using aggregation functions
CREATE OR REPLACE VIEW MYDB.PUBLIC.VW_DAILY_SALES AS
SELECT
    DATE_TRUNC('day', o.ORDER_DATE)               AS SALE_DATE,
    o.REGION,
    COUNT(*)                                       AS ORDER_COUNT,
    SUM(o.AMOUNT)                                  AS TOTAL_REVENUE,
    MEDIAN(o.AMOUNT)                               AS MEDIAN_ORDER_VALUE,
    APPROX_PERCENTILE(o.AMOUNT, 0.95)             AS P95_ORDER_VALUE,
    LISTAGG(DISTINCT o.STATUS, ', ')
        WITHIN GROUP (ORDER BY o.STATUS)           AS STATUSES,
    ARRAY_AGG(DISTINCT o.CUSTOMER_ID)             AS UNIQUE_CUSTOMERS,
    COUNT_IF(o.AMOUNT > 1000)                      AS HIGH_VALUE_ORDERS,
    DIV0(SUM(o.AMOUNT), COUNT(*))                 AS AVG_ORDER_SAFE,
    SKEW(o.AMOUNT)                                 AS AMOUNT_SKEWNESS
FROM MYDB.PUBLIC.ORDERS o
WHERE o.STATUS != 'CANCELLED'
GROUP BY 1, 2;


-- Materialized view
CREATE OR REPLACE MATERIALIZED VIEW MYDB.PUBLIC.MV_PRODUCT_STATS AS
SELECT
    PRODUCT_ID,
    COUNT(*)                                       AS TOTAL_ORDERS,
    SUM(AMOUNT)                                    AS TOTAL_REVENUE,
    MAX(ORDER_DATE)                                AS LAST_ORDER_DATE
FROM MYDB.PUBLIC.ORDERS
GROUP BY PRODUCT_ID;
