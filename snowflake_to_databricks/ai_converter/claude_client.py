"""
Claude API client for complex SQL conversions.
Uses claude-sonnet-4-6 with retry logic.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

import anthropic

from snowflake_to_databricks.models import ObjectType

_SYSTEM_PROMPT = """You are an expert SQL migration engineer specializing in converting Snowflake SQL to Databricks SQL (Spark SQL / Delta Lake).

Your output must be syntactically correct Databricks SQL that runs without compilation errors.

Rules:
1. Output the converted SQL inside a ```sql ... ``` code block.
2. After the SQL block, output a JSON block: ```json { "warnings": [...], "notes": "..." } ```
3. Follow these conversion rules strictly:
   - Add USING DELTA to all CREATE TABLE statements
   - NEVER use Snowflake-specific syntax: no VARIANT type, NVL(), ZEROIFNULL(), IFF() (use IF()), LATERAL FLATTEN (use LATERAL VIEW EXPLODE), $$ delimiters, LANGUAGE JAVASCRIPT, OBJECT_CONSTRUCT (use NAMED_STRUCT or TO_JSON(STRUCT(...)))
   - ARRAY_CONTAINS(arr, val) — array comes FIRST in Databricks (reversed from Snowflake)
   - DECODE → CASE WHEN; NULL values need IS NULL not = NULL
   - TO_CHAR(date, fmt) → DATE_FORMAT(date, fmt) with lowercase format strings (YYYY→yyyy, DD→dd, HH24→HH, MI→mm)
   - TRUNC(date, part) → DATE_TRUNC(part, date) — args are reversed
   - CONNECT BY → WITH RECURSIVE CTE
   - JavaScript stored procedures → Python UDF or Databricks SQL procedure (must include LANGUAGE SQL and SQL SECURITY INVOKER)
   - Colon-path notation col:key → GET_JSON_OBJECT(col, '$.key')
   - PARSE_JSON(str) → FROM_JSON(str, schema) — infer schema from context
   - LISTAGG, DATEDIFF(part,d1,d2), IFF — these are compatible, keep as-is
4. Add inline comments /* MANUAL_REVIEW: reason */ for any pattern you cannot convert with certainty.
5. Preserve ALL business logic exactly — never change the semantic meaning.
"""


def _extract_sql(text: str) -> str:
    """Extract SQL from ```sql ... ``` block."""
    m = re.search(r'```sql\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Fallback: return everything if no code block found
    return text.strip()


def _extract_warnings(text: str) -> list[str]:
    """Extract warnings from ```json ... ``` block."""
    m = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
        return data.get("warnings", [])
    except json.JSONDecodeError:
        return []


class ClaudeClient:
    MODEL = "claude-sonnet-4-6"
    MAX_TOKENS = 8096
    MAX_RETRIES = 3

    def __init__(self, api_key: str):
        self._client = anthropic.Anthropic(api_key=api_key)

    def convert(
        self,
        sql: str,
        original_sql: str,
        object_type: ObjectType,
        escalation_reasons: Optional[list[str]] = None,
    ) -> tuple[str, int]:
        """
        Convert partially-converted SQL (after rule engine) using Claude.
        Returns (converted_sql, tokens_used).
        """
        reasons_str = ""
        if escalation_reasons:
            reasons_str = (
                "\n\nPatterns that still need conversion:\n"
                + "\n".join(f"- {r}" for r in escalation_reasons)
            )

        prompt = (
            f"Convert this Snowflake {object_type.value.upper()} to Databricks SQL.\n\n"
            f"The rule-based pre-processor has already applied some conversions. "
            f"Complete the remaining conversions:{reasons_str}\n\n"
            f"<snowflake_original>\n{original_sql}\n</snowflake_original>\n\n"
            f"<partially_converted>\n{sql}\n</partially_converted>"
        )

        response_text, tokens = self._call_api(prompt)
        return _extract_sql(response_text), tokens

    def convert_procedure(
        self,
        sql: str,
        language: str,
    ) -> tuple[str, int]:
        """Convert a stored procedure (especially JavaScript) using Claude."""
        target = (
            "a Databricks SQL procedure (LANGUAGE SQL, SQL SECURITY INVOKER)"
            if language in ("sql", "javascript_udf")
            else "a Python UDF (LANGUAGE PYTHON) or Databricks SQL procedure"
        )
        prompt = (
            f"Convert this Snowflake {language.upper()} stored procedure / UDF to {target}.\n\n"
            "Requirements:\n"
            "- Preserve ALL business logic exactly\n"
            "- SQL procedures must include LANGUAGE SQL and SQL SECURITY INVOKER\n"
            "- Python UDFs must use LANGUAGE PYTHON with valid Python body\n"
            "- Add MANUAL_REVIEW comments for any uncertain conversions\n\n"
            f"<snowflake_procedure>\n{sql}\n</snowflake_procedure>"
        )

        response_text, tokens = self._call_api(prompt)
        return _extract_sql(response_text), tokens

    def _call_api(self, prompt: str) -> tuple[str, int]:
        """Call Claude API with exponential backoff retry."""
        last_exc: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES):
            try:
                msg = self._client.messages.create(
                    model=self.MODEL,
                    max_tokens=self.MAX_TOKENS,
                    system=_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = msg.content[0].text
                tokens = msg.usage.input_tokens + msg.usage.output_tokens
                return text, tokens
            except anthropic.APIStatusError as exc:
                last_exc = exc
                if exc.status_code in (429, 529):  # rate limit / overload
                    wait = 2 ** attempt
                    time.sleep(wait)
                else:
                    raise
            except anthropic.APIConnectionError as exc:
                last_exc = exc
                time.sleep(2 ** attempt)

        raise RuntimeError(f"Claude API failed after {self.MAX_RETRIES} retries: {last_exc}")
