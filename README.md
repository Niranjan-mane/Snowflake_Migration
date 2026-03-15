# Snowflake → Databricks Migration Agent

An end-to-end migration agent that converts Snowflake SQL objects to Databricks-compatible SQL using rule-based transformations + Claude AI for complex patterns, validates the output, and deploys objects to Databricks in dependency order.

## Features

- **Extract** all object DDLs from Snowflake (tables, views, materialized views, stored procedures, functions, sequences, stages, streams, tasks)
- **Convert** Snowflake SQL to Databricks SQL:
  - 70+ function mappings (NVL→COALESCE, ARRAY_AGG→COLLECT_LIST, TRUNC→DATE_TRUNC, etc.)
  - Data type conversions (NUMBER→DECIMAL, VARIANT→STRING, TIMESTAMP_TZ→TIMESTAMP, etc.)
  - DDL transformations (TRANSIENT TABLE removal, AUTOINCREMENT→GENERATED ALWAYS AS IDENTITY, etc.)
  - Delta Lake integration: automatic `USING DELTA` + `TBLPROPERTIES` on all tables
  - Claude AI (claude-sonnet-4-6) for complex patterns (JavaScript procedures, CONNECT BY, OBJECT_CONSTRUCT)
- **Validate** converted SQL using `sqlglot` and residual Snowflake pattern scanning
- **Deploy** to Databricks in dependency order (Sequences → Tables → Views → Functions → Procedures)
- **Report** conversion results as a JSON file and rich terminal table

---

## Installation

```bash
git clone <repo-url>
cd Snowflake_Migration
pip install -r requirements.txt
pip install -e .
```

---

## Configuration

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

The agent reads from three YAML config files in `config/`:

### `config/snowflake.yaml`
```yaml
snowflake:
  account:   ${SF_ACCOUNT}         # e.g. myorg-myaccount (new format)
  user:      ${SF_USER}
  password:  ${SF_PASSWORD}
  warehouse: ${SF_WAREHOUSE}
  database:  ${SF_DATABASE}
  schema:    ${SF_SCHEMA}
  role:      ${SF_ROLE}
  auth_method: password             # password | keypair | sso | oauth
```

### `config/databricks.yaml`
```yaml
databricks:
  host:         ${DB_HOST}          # adb-xxx.azuredatabricks.net
  http_path:    ${DB_HTTP_PATH}     # /sql/1.0/warehouses/xxx
  access_token: ${DB_ACCESS_TOKEN}  # dapi...
  catalog:      ${DB_CATALOG}
  schema:       ${DB_SCHEMA}
```

### `config/conversion.yaml`
```yaml
conversion:
  use_ai: true
  anthropic_api_key: ${ANTHROPIC_API_KEY}
  report_path: ./migration_report.json
  delta_lake:
    always_use_delta: true
    add_auto_optimize: true
    default_retention_days: 7
```

---

## Usage

### Full migration pipeline

```bash
# All-in-one: extract → convert → validate → deploy → report
snowflake-to-databricks migrate --config-dir config/

# Dry run (no actual deployment)
snowflake-to-databricks migrate --config-dir config/ --dry-run
```

### Step-by-step

```bash
# 1. Extract all objects from Snowflake
snowflake-to-databricks extract \
  --config-dir config/ \
  --output-dir ./extracted/

# 2. Convert to Databricks SQL
snowflake-to-databricks convert \
  --config-dir config/ \
  --input-dir ./extracted/ \
  --output-dir ./converted/

# 3. Validate converted SQL
snowflake-to-databricks validate \
  --input-dir ./converted/

# 4. Deploy to Databricks
snowflake-to-databricks deploy \
  --config-dir config/ \
  --input-dir ./converted/ \
  --dry-run          # Remove --dry-run to actually deploy
```

### Convert a single SQL string (programmatic)

```python
from snowflake_to_databricks.agent import MigrationAgent

agent = MigrationAgent(config_dir="config")
results = agent.convert_sql("""
    CREATE TRANSIENT TABLE orders (
        id NUMBER(38,0) AUTOINCREMENT,
        amount FLOAT8,
        tags VARIANT,
        created_at TIMESTAMP_LTZ
    ) DATA_RETENTION_TIME_IN_DAYS = 7;
""")

for r in results:
    print(r.converted_sql)
    print(f"Status: {r.status.value}, Changes: {r.change_count}")
```

---

## What Gets Converted

### Data Types

| Snowflake | Databricks |
|-----------|-----------|
| `NUMBER(p,s)` | `DECIMAL(p,s)` |
| `BYTEINT` | `TINYINT` |
| `FLOAT8` | `DOUBLE` |
| `VARCHAR` | `STRING` |
| `TEXT` | `STRING` |
| `VARIANT` | `STRING` |
| `OBJECT` | `MAP<STRING,STRING>` |
| `TIMESTAMP_NTZ` | `TIMESTAMP_NTZ` |
| `TIMESTAMP_LTZ` / `TIMESTAMP_TZ` | `TIMESTAMP` |
| `DATETIME` | `TIMESTAMP` |
| `TIME` | `STRING` |
| `GEOGRAPHY` / `GEOMETRY` | `STRING` (WKT) |

### Functions (70+ mappings)

| Snowflake | Databricks |
|-----------|-----------|
| `NVL(x, y)` | `COALESCE(x, y)` |
| `IFF(c, t, f)` | `IF(c, t, f)` |
| `TRUNC(d, 'MONTH')` | `DATE_TRUNC('MONTH', d)` |
| `ARRAY_AGG(x)` | `COLLECT_LIST(x)` |
| `ARRAY_CONTAINS(val, arr)` | `ARRAY_CONTAINS(arr, val)` *(arg swap)* |
| `LISTAGG(col, sep)` | `LISTAGG(col, sep)` *(native in Databricks)* |
| `QUALIFY ...` | Native in Databricks 12.2+ |
| `col:key::STRING` | `GET_JSON_OBJECT(col, '$.key')` |
| `OBJECT_CONSTRUCT(k, v)` | `NAMED_STRUCT(k, v)` |
| `SYSDATE()` | `CURRENT_TIMESTAMP()` |
| `DIV0(n, d)` | `IF(d=0, 0, n/d)` |
| `SKEW(x)` | `SKEWNESS(x)` |

### DDL Transformations

- `CREATE TRANSIENT TABLE` → `CREATE TABLE` (Delta)
- `AUTOINCREMENT` / `IDENTITY(s,i)` → `GENERATED ALWAYS AS IDENTITY (START WITH s INCREMENT BY i)`
- `DATA_RETENTION_TIME_IN_DAYS = N` → `TBLPROPERTIES('delta.deletedFileRetentionDuration'='interval N days')`
- `COPY GRANTS` → removed
- `TAG (k=v)` → removed
- `CREATE SECURE VIEW` → `CREATE VIEW`
- `CREATE MATERIALIZED VIEW` → `-- MANUAL_REVIEW: use Delta Live Tables`
- `CREATE STAGE/TASK/PIPE` → `-- MANUAL_REVIEW:` comments
- `AT(TIMESTAMP => ts)` → `TIMESTAMP AS OF ts` (Delta time travel)
- `LATERAL FLATTEN(input => arr)` → `LATERAL VIEW EXPLODE(arr)`

### Delta Lake (all tables)

Every `CREATE TABLE` automatically gets:
```sql
USING DELTA
TBLPROPERTIES (
  'delta.deletedFileRetentionDuration' = 'interval 7 days',
  'delta.autoOptimize.optimizeWrite'   = 'true',
  'delta.autoOptimize.autoCompact'     = 'true'
)
```

---

## Stored Procedure Conversion

| Language | Strategy |
|----------|----------|
| SQL Scripting | Rule-based: `LET x :=` → `SET VAR x =`, `EXECUTE AS CALLER` → `SQL SECURITY INVOKER` |
| JavaScript | Claude AI converts to Python UDF / SQL procedure |
| Python (Snowpark) | Adapt `snowflake.snowpark` → `pyspark` imports |
| Java / Scala | Claude AI required; flagged for manual review |

---

## Validation

Three-layer validation:
1. **sqlglot** — Parse in Databricks dialect; catch syntax errors
2. **Residual scan** — Regex check for unconverted Snowflake patterns (NVL, ARRAY_AGG, LATERAL FLATTEN, `$` refs, etc.)
3. **Databricks EXPLAIN** — Optional pre-deployment compilation check (requires connection)

---

## Migration Report

After each run, a JSON report is saved to `./migration_report.json`:

```json
{
  "summary": {
    "total_objects": 50,
    "converted": 45,
    "needs_review": 3,
    "failed": 2,
    "deployed": 43,
    "ai_calls": 8,
    "total_ai_tokens": 24500
  },
  "objects": [
    {
      "name": "CUSTOMER_ORDERS",
      "type": "table",
      "status": "converted",
      "method": "rule_based",
      "change_count": 7,
      "changes": [...]
    }
  ]
}
```

---

## Running Tests

```bash
# All tests (no credentials required)
pytest tests/ -v

# Specific test file
pytest tests/test_function_mappings.py -v

# With coverage
pytest tests/ --cov=snowflake_to_databricks --cov-report=term-missing
```

---

## Deployment Order

Objects are deployed to Databricks in this order to respect dependencies:

1. Sequences
2. Tables
3. Views
4. Materialized Views
5. Functions
6. Procedures
7. Streams (CDF enabled)
8. Stages
9. Tasks
10. DML statements

Objects with status `NEEDS_REVIEW` are automatically skipped during deployment.

---

## Authentication Options

### Snowflake
- `password` — Username + password
- `keypair` — RSA key-pair (set `private_key_path` in config or `SF_PRIVATE_KEY_PATH` env var)
- `sso` — External browser SSO (`authenticator: externalbrowser`)
- `oauth` — OAuth token (`SF_OAUTH_TOKEN` env var)

### Databricks
- **PAT** — `access_token: dapi...` (default)
- **OAuth** — Set `auth_method: oauth` in databricks.yaml; uses environment credential providers
- **Azure AD** — Set `auth_method: azure_ad`

---

## Project Structure

```
Snowflake_Migration/
├── config/
│   ├── snowflake.yaml       # Snowflake connection + extraction settings
│   ├── databricks.yaml      # Databricks connection + deploy settings
│   └── conversion.yaml      # AI, Delta Lake, and conversion options
├── snowflake_to_databricks/
│   ├── agent.py             # Top-level MigrationAgent orchestrator
│   ├── models.py            # Shared dataclasses and enums
│   ├── cli/main.py          # Click CLI entry point
│   ├── rules/               # Regex conversion rules
│   │   ├── rule_engine.py
│   │   ├── type_mappings.py
│   │   ├── function_mappings.py
│   │   ├── syntax_rules.py
│   │   └── ddl_rules.py
│   ├── converters/          # Object-type-specific converters
│   │   ├── base.py
│   │   ├── table_converter.py
│   │   ├── view_converter.py
│   │   ├── dml_converter.py
│   │   ├── procedure_converter.py
│   │   └── function_converter.py
│   ├── ai_converter/        # Claude API integration
│   │   └── claude_client.py
│   ├── validators/          # sqlglot + residual pattern checks
│   │   └── sql_validator.py
│   ├── connectors/          # Snowflake + Databricks connections
│   │   ├── snowflake_connector.py
│   │   └── databricks_connector.py
│   ├── extractor/           # GET_DDL + INFORMATION_SCHEMA queries
│   │   └── snowflake_extractor.py
│   ├── deployer/            # Dependency-ordered deployment
│   │   └── databricks_deployer.py
│   └── reporter/            # JSON + Rich terminal report
│       └── report_generator.py
├── samples/                 # Example Snowflake SQL files
├── tests/                   # Unit tests (no credentials needed)
├── .env.example
├── requirements.txt
└── README.md
```
