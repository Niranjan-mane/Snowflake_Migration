"""
Function conversion rules: Snowflake → Databricks.

Research-validated corrections:
- LISTAGG: Databricks supports it natively — NO conversion needed
- DATEDIFF(part, start, end): same 3-arg signature — NO conversion needed
- IFF: Databricks supports IFF as synonym for IF — NO conversion needed
- QUALIFY: Databricks 12.2+ supports it natively — NO conversion needed
- ARRAY_CONTAINS: args are REVERSED (Snowflake: val,arr → Databricks: arr,val)
- DECODE: NULL handling differs — must use IS NULL in CASE WHEN
- TO_CHAR: format string must be lowercased (YYYY→yyyy, MI→mm, etc.)
- TRUNC(date, part): args REVERSED in Databricks → DATE_TRUNC(part, date)
"""
from __future__ import annotations

import re
from .rule_engine import Rule

FLAGS = re.IGNORECASE | re.MULTILINE | re.DOTALL


# ---------------------------------------------------------------------------
# Callable helpers for complex rewrites
# ---------------------------------------------------------------------------

def _decode_to_case(m: re.Match) -> str:
    """
    Convert DECODE(expr, val1, res1, val2, res2, ..., default)
    to CASE WHEN expr=val1 THEN res1 ... ELSE default END.
    NULL values in DECODE match NULL — must use IS NULL.
    """
    inner = m.group(1).strip()
    # Simple tokenise by comma (not inside nested parens/quotes)
    parts = _split_args(inner)
    if len(parts) < 3:
        return m.group(0)  # can't parse — leave as-is

    expr = parts[0].strip()
    when_clauses = []
    i = 1
    while i + 1 < len(parts):
        val = parts[i].strip()
        res = parts[i + 1].strip()
        # NULL matching: DECODE matches NULL; CASE WHEN must use IS NULL
        if val.upper() == 'NULL':
            when_clauses.append(f"WHEN {expr} IS NULL THEN {res}")
        else:
            when_clauses.append(f"WHEN {expr} = {val} THEN {res}")
        i += 2

    else_clause = f"ELSE {parts[-1].strip()}" if len(parts) % 2 == 0 else ""
    when_str = " ".join(when_clauses)
    return f"CASE {when_str} {else_clause} END"


def _colon_path_to_json(m: re.Match) -> str:
    """
    Convert Snowflake colon-path notation col:key:subkey[::TYPE]
    to GET_JSON_OBJECT(col, '$.key.subkey').
    """
    full = m.group(0)
    # Strip type cast suffix ::TYPE
    cast_match = re.search(r'::\s*(\w+)\s*$', full)
    cast_type = None
    if cast_match:
        cast_type = cast_match.group(1).upper()
        full = full[:cast_match.start()]

    parts = re.split(r':', full)
    col = parts[0].strip()
    path = '.'.join(p.strip() for p in parts[1:])
    result = f"GET_JSON_OBJECT({col}, '$.{path}')"

    if cast_type and cast_type != 'VARIANT':
        type_map = {
            'STRING': 'STRING', 'VARCHAR': 'STRING', 'TEXT': 'STRING',
            'INT': 'INT', 'INTEGER': 'INT', 'BIGINT': 'BIGINT',
            'FLOAT': 'DOUBLE', 'DOUBLE': 'DOUBLE',
            'BOOLEAN': 'BOOLEAN', 'BOOL': 'BOOLEAN',
            'NUMBER': 'DECIMAL', 'DECIMAL': 'DECIMAL',
        }
        db_type = type_map.get(cast_type, cast_type)
        result = f"CAST({result} AS {db_type})"

    return result


def _array_contains_swap(m: re.Match) -> str:
    """
    CRITICAL: Snowflake ARRAY_CONTAINS(value, array) → Databricks ARRAY_CONTAINS(array, value)
    Argument order is REVERSED.
    """
    args = _split_args(m.group(1))
    if len(args) == 2:
        return f"ARRAY_CONTAINS({args[1].strip()}, {args[0].strip()})"
    return m.group(0)


def _array_position_swap(m: re.Match) -> str:
    """
    CRITICAL: Snowflake ARRAY_POSITION(value, array) → Databricks ARRAY_POSITION(array, value)
    """
    args = _split_args(m.group(1))
    if len(args) == 2:
        return f"ARRAY_POSITION({args[1].strip()}, {args[0].strip()})"
    return m.group(0)


def _trunc_date(m: re.Match) -> str:
    """
    TRUNC(date, 'part') → DATE_TRUNC('part', date)  [arg order reversed]
    """
    args = _split_args(m.group(1))
    if len(args) == 2:
        return f"DATE_TRUNC({args[1].strip()}, {args[0].strip()})"
    # Single-arg TRUNC (numeric) — leave as-is
    return f"TRUNC({m.group(1)})"


def _to_char_format(m: re.Match) -> str:
    """
    TO_CHAR(expr, 'FORMAT') → DATE_FORMAT(expr, 'format')
    Convert Snowflake uppercase format specifiers to Java/Databricks lowercase.
    """
    expr = m.group(1).strip()
    fmt = m.group(2).strip()

    # Format string substitutions (Snowflake → Java SimpleDateFormat)
    fmt_map = [
        ('YYYY', 'yyyy'), ('YYY', 'yyy'), ('YY', 'yy'), ('Y', 'y'),
        ('DD', 'dd'), ('D', 'd'),
        ('HH24', 'HH'), ('HH12', 'hh'), ('HH', 'hh'),
        ('MI', 'mm'),
        ('SS', 'ss'),
        ('FF9', 'SSSSSSSSS'), ('FF6', 'SSSSSS'), ('FF3', 'SSS'), ('FF', 'SSS'),
        ('MONTH', 'MMMM'), ('MON', 'MMM'),
        ('DAY', 'EEEE'), ('DY', 'EEE'),
        ('AM', 'a'), ('PM', 'a'),
        ('TZH:TZM', 'xxx'), ('TZH', 'xx'), ('TZM', 'x'),
    ]
    for sf, db in fmt_map:
        fmt = re.sub(re.escape(sf), db, fmt, flags=re.IGNORECASE)

    return f"DATE_FORMAT({expr}, {fmt})"


def _split_args(text: str) -> list[str]:
    """Split function arguments by comma, respecting nested parens and quotes."""
    args = []
    depth = 0
    current = []
    in_single = False
    in_double = False

    for ch in text:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif ch == ',' and depth == 0:
                args.append(''.join(current))
                current = []
                continue
        current.append(ch)

    if current:
        args.append(''.join(current))
    return args


# ---------------------------------------------------------------------------
# Function rules
# ---------------------------------------------------------------------------

FUNCTION_RULES: list[Rule] = [

    # =========================================================
    # NULL / CONDITIONAL (no conversion needed for IFF)
    # =========================================================
    Rule(
        name="NVL",
        pattern=re.compile(r'\bNVL\s*\(', FLAGS),
        replacement='COALESCE(',
        confidence="high",
    ),
    Rule(
        name="IFNULL",
        pattern=re.compile(r'\bIFNULL\s*\(', FLAGS),
        replacement='COALESCE(',
        confidence="high",
    ),
    Rule(
        name="NVL2",
        pattern=re.compile(r'\bNVL2\s*\(([^,]+),\s*([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'IF(\1 IS NOT NULL, \2, \3)',
        confidence="high",
    ),
    Rule(
        name="ZEROIFNULL",
        pattern=re.compile(r'\bZEROIFNULL\s*\(([^)]+)\)', FLAGS),
        replacement=r'COALESCE(\1, 0)',
        confidence="high",
    ),
    Rule(
        name="NULLIFZERO",
        pattern=re.compile(r'\bNULLIFZERO\s*\(([^)]+)\)', FLAGS),
        replacement=r'NULLIF(\1, 0)',
        confidence="high",
    ),
    Rule(
        name="EQUAL_NULL",
        pattern=re.compile(r'\bEQUAL_NULL\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'(\1 <=> \2)',
        confidence="high",
    ),
    Rule(
        name="BOOLAND_AGG",
        pattern=re.compile(r'\bBOOLAND_AGG\s*\(', FLAGS),
        replacement='BOOL_AND(',
        confidence="high",
    ),
    Rule(
        name="BOOLOR_AGG",
        pattern=re.compile(r'\bBOOLOR_AGG\s*\(', FLAGS),
        replacement='BOOL_OR(',
        confidence="high",
    ),
    Rule(
        name="DECODE",
        pattern=re.compile(r'\bDECODE\s*\((.+?)\)', FLAGS),
        replacement=_decode_to_case,
        confidence="medium",
        note="DECODE NULL matching differs — IS NULL used in CASE WHEN",
    ),

    # =========================================================
    # STRING FUNCTIONS
    # =========================================================
    Rule(
        name="CHARINDEX_with_pos",
        pattern=re.compile(r'\bCHARINDEX\s*\(([^,]+),\s*([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'LOCATE(\1, \2, \3)',
        confidence="high",
    ),
    Rule(
        name="CHARINDEX",
        pattern=re.compile(r'\bCHARINDEX\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'LOCATE(\1, \2)',
        confidence="high",
    ),
    Rule(
        name="EDITDISTANCE",
        pattern=re.compile(r'\bEDITDISTANCE\s*\(', FLAGS),
        replacement='LEVENSHTEIN(',
        confidence="high",
    ),
    Rule(
        name="CONTAINS_string",
        pattern=re.compile(r'\bCONTAINS\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r"\1 LIKE CONCAT('%', \2, '%')",
        confidence="high",
    ),
    Rule(
        name="REGEXP_LIKE",
        pattern=re.compile(r'\bREGEXP_LIKE\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'\1 RLIKE \2',
        confidence="high",
    ),
    Rule(
        name="REGEXP_SUBSTR",
        pattern=re.compile(r'\bREGEXP_SUBSTR\s*\(([^,]+),\s*([^,)]+)\)', FLAGS),
        replacement=r'REGEXP_EXTRACT(\1, \2, 0)',
        confidence="medium",
        note="REGEXP_SUBSTR → REGEXP_EXTRACT; complex multi-arg form may need review",
    ),
    Rule(
        name="REGEXP_COUNT",
        pattern=re.compile(r'\bREGEXP_COUNT\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'(SIZE(SPLIT(\1, \2)) - 1)',
        confidence="medium",
        note="Approximate; edge cases with special regex chars may differ",
    ),
    Rule(
        name="STRTOK_TO_TABLE",
        pattern=re.compile(r'\bSTRTOK_TO_TABLE\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'LATERAL VIEW EXPLODE(SPLIT(\1, \2)) t AS value',
        confidence="medium",
    ),
    Rule(
        name="STRTOK",
        pattern=re.compile(r'\bSTRTOK\s*\(([^,]+),\s*([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'SPLIT(\1, \2)[\3 - 1]',
        confidence="medium",
        note="STRTOK 1-indexed → SPLIT 0-indexed; adjust if position is variable",
    ),
    Rule(
        name="TO_VARCHAR",
        pattern=re.compile(r'\bTO_VARCHAR\s*\(([^,)]+)\)', FLAGS),
        replacement=r'CAST(\1 AS STRING)',
        confidence="high",
    ),
    Rule(
        name="TO_CHAR_with_format",
        pattern=re.compile(r'\bTO_CHAR\s*\(([^,]+),\s*(\'[^\']+\'|"[^"]+")\)', FLAGS),
        replacement=_to_char_format,
        confidence="high",
        note="Format specifiers converted: YYYY→yyyy, DD→dd, HH24→HH, MI→mm",
    ),
    Rule(
        name="TO_CHAR_no_format",
        pattern=re.compile(r'\bTO_CHAR\s*\(([^)]+)\)', FLAGS),
        replacement=r'CAST(\1 AS STRING)',
        confidence="high",
    ),
    Rule(
        name="BASE64_ENCODE",
        pattern=re.compile(r'\bBASE64_ENCODE\s*\(', FLAGS),
        replacement='BASE64(',
        confidence="high",
    ),
    Rule(
        name="BASE64_DECODE_STRING",
        pattern=re.compile(r'\bBASE64_DECODE_STRING\s*\(', FLAGS),
        replacement='UNBASE64(',
        confidence="high",
    ),
    Rule(
        name="HEX_ENCODE",
        pattern=re.compile(r'\bHEX_ENCODE\s*\(', FLAGS),
        replacement='HEX(',
        confidence="high",
    ),
    Rule(
        name="UNICODE_fn",
        pattern=re.compile(r'\bUNICODE\s*\(', FLAGS),
        replacement='ASCII(',
        confidence="medium",
        note="ASCII returns code of first character only",
    ),

    # =========================================================
    # NUMERIC FUNCTIONS
    # =========================================================
    Rule(
        name="DIV0",
        pattern=re.compile(r'\bDIV0\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'IF(\2 = 0, 0, \1 / \2)',
        confidence="high",
    ),
    Rule(
        name="DIV0NULL",
        pattern=re.compile(r'\bDIV0NULL\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'IF(\2 = 0, NULL, \1 / \2)',
        confidence="high",
    ),
    Rule(
        name="SQUARE",
        pattern=re.compile(r'\bSQUARE\s*\(([^)]+)\)', FLAGS),
        replacement=r'POW(\1, 2)',
        confidence="high",
    ),
    Rule(
        name="RANDOM_fn",
        pattern=re.compile(r'\bRANDOM\s*\(\s*\)', FLAGS),
        replacement='RAND()',
        confidence="high",
    ),
    Rule(
        name="BITAND_fn",
        pattern=re.compile(r'\bBITAND\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'(\1 & \2)',
        confidence="high",
    ),
    Rule(
        name="BITOR_fn",
        pattern=re.compile(r'\bBITOR\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'(\1 | \2)',
        confidence="high",
    ),
    Rule(
        name="BITXOR_fn",
        pattern=re.compile(r'\bBITXOR\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'(\1 ^ \2)',
        confidence="high",
    ),
    Rule(
        name="BITNOT_fn",
        pattern=re.compile(r'\bBITNOT\s*\(([^)]+)\)', FLAGS),
        replacement=r'(~\1)',
        confidence="high",
    ),
    Rule(
        name="BITSHIFTLEFT",
        pattern=re.compile(r'\bBITSHIFTLEFT\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'(\1 << \2)',
        confidence="high",
    ),
    Rule(
        name="BITSHIFTRIGHT",
        pattern=re.compile(r'\bBITSHIFTRIGHT\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'(\1 >> \2)',
        confidence="high",
    ),
    Rule(
        name="SEQ_functions",
        pattern=re.compile(r'\bSEQ\d+\s*\(\s*\)', FLAGS),
        replacement='MONOTONICALLY_INCREASING_ID()',
        confidence="medium",
        note="SEQ functions → MONOTONICALLY_INCREASING_ID() generates unique 64-bit IDs per row; values are not sequential but unique across partitions",
    ),
    Rule(
        name="UNIFORM_fn",
        # (?:[^()]|\([^)]*\))* handles one level of nesting (e.g. RAND() as the gen arg)
        pattern=re.compile(
            r'\bUNIFORM\s*\(([^,]+),\s*([^,]+),\s*(?:[^()]|\([^)]*\))*\)',
            FLAGS,
        ),
        # Drop the Snowflake generator arg entirely; RAND() needs no seed expression
        replacement=r'FLOOR(\1 + RAND() * (\2 - \1 + 1))',
        confidence="medium",
        note="UNIFORM(min, max, gen) → FLOOR(min + RAND() * (max - min + 1)); generator arg dropped",
    ),

    # =========================================================
    # DATE / TIME FUNCTIONS
    # NOTE: DATEDIFF(part, start, end) — SAME in both. No conversion.
    # NOTE: LISTAGG — SAME in both. No conversion.
    # =========================================================
    Rule(
        name="DATE_FROM_PARTS",
        pattern=re.compile(r'\bDATE_FROM_PARTS\s*\(', FLAGS),
        replacement='MAKE_DATE(',
        confidence="high",
    ),
    Rule(
        name="TIMESTAMP_FROM_PARTS",
        pattern=re.compile(r'\bTIMESTAMP_FROM_PARTS\s*\(', FLAGS),
        replacement='MAKE_TIMESTAMP(',
        confidence="high",
    ),
    Rule(
        name="TIME_FROM_PARTS",
        pattern=re.compile(r'\bTIME_FROM_PARTS\s*\(', FLAGS),
        replacement='MAKE_TIMESTAMP(',
        confidence="medium",
        note="TIME_FROM_PARTS → MAKE_TIMESTAMP; check usage context",
    ),
    Rule(
        name="TRUNC_date",
        pattern=re.compile(r'\bTRUNC\s*\((.+?)\)', FLAGS),
        replacement=_trunc_date,
        confidence="high",
        note="TRUNC(date, part) arg order reversed → DATE_TRUNC(part, date)",
    ),
    Rule(
        name="MONTHNAME",
        pattern=re.compile(r'\bMONTHNAME\s*\(([^)]+)\)', FLAGS),
        replacement=r"DATE_FORMAT(\1, 'MMMM')",
        confidence="high",
    ),
    Rule(
        name="DAYNAME",
        pattern=re.compile(r'\bDAYNAME\s*\(([^)]+)\)', FLAGS),
        replacement=r"DATE_FORMAT(\1, 'EEEE')",
        confidence="high",
    ),
    Rule(
        name="SYSDATE",
        pattern=re.compile(r'\bSYSDATE\s*\(\s*\)', FLAGS),
        replacement='CURRENT_TIMESTAMP()',
        confidence="high",
    ),
    Rule(
        name="GETDATE",
        pattern=re.compile(r'\bGETDATE\s*\(\s*\)', FLAGS),
        replacement='CURRENT_TIMESTAMP()',
        confidence="high",
    ),
    Rule(
        name="TO_TIME",
        pattern=re.compile(r'\bTO_TIME\s*\(([^)]+)\)', FLAGS),
        replacement=r'CAST(\1 AS TIMESTAMP)',
        confidence="medium",
        note="Databricks has no TIME type; cast to TIMESTAMP",
    ),

    # =========================================================
    # AGGREGATE FUNCTIONS
    # NOTE: LISTAGG, DATEDIFF, STDDEV, VARIANCE, CORR — compatible
    # =========================================================
    Rule(
        name="ARRAY_AGG",
        pattern=re.compile(r'\bARRAY_AGG\s*\(DISTINCT\s+', FLAGS),
        replacement='COLLECT_SET(',
        confidence="high",
    ),
    Rule(
        name="ARRAY_AGG_plain",
        pattern=re.compile(r'\bARRAY_AGG\s*\(', FLAGS),
        replacement='COLLECT_LIST(',
        confidence="high",
    ),
    Rule(
        name="OBJECT_AGG",
        pattern=re.compile(r'\bOBJECT_AGG\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'MAP_FROM_ENTRIES(COLLECT_LIST(STRUCT(\1, \2)))',
        confidence="medium",
    ),
    Rule(
        name="MEDIAN_fn",
        pattern=re.compile(r'\bMEDIAN\s*\(([^)]+)\)', FLAGS),
        replacement=r'PERCENTILE_APPROX(\1, 0.5)',
        confidence="high",
    ),
    Rule(
        name="APPROX_PERCENTILE",
        pattern=re.compile(r'\bAPPROX_PERCENTILE\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'PERCENTILE_APPROX(\1, \2)',
        confidence="high",
    ),
    Rule(
        name="HLL_fn",
        pattern=re.compile(r'\bHLL\s*\(', FLAGS),
        replacement='APPROX_COUNT_DISTINCT(',
        confidence="high",
    ),
    Rule(
        name="SKEW_fn",
        pattern=re.compile(r'\bSKEW\s*\(', FLAGS),
        replacement='SKEWNESS(',
        confidence="high",
    ),
    Rule(
        name="RATIO_TO_REPORT",
        pattern=re.compile(r'\bRATIO_TO_REPORT\s*\(([^)]+)\)\s*OVER\s*\(([^)]*)\)', FLAGS),
        replacement=r'\1 / SUM(\1) OVER (\2)',
        confidence="high",
    ),

    # =========================================================
    # SEMI-STRUCTURED / JSON
    # =========================================================

    # CRITICAL: ARRAY_CONTAINS arg order is REVERSED
    Rule(
        name="ARRAY_CONTAINS",
        pattern=re.compile(r'\bARRAY_CONTAINS\s*\(([^)]+)\)', FLAGS),
        replacement=_array_contains_swap,
        confidence="high",
        note="CRITICAL: Snowflake ARRAY_CONTAINS(value, array) → Databricks ARRAY_CONTAINS(array, value)",
    ),
    # CRITICAL: ARRAY_POSITION arg order is REVERSED
    Rule(
        name="ARRAY_POSITION",
        pattern=re.compile(r'\bARRAY_POSITION\s*\(([^)]+)\)', FLAGS),
        replacement=_array_position_swap,
        confidence="high",
        note="CRITICAL: Snowflake ARRAY_POSITION(value, array) → Databricks ARRAY_POSITION(array, value)",
    ),
    Rule(
        name="ARRAY_SIZE",
        pattern=re.compile(r'\bARRAY_SIZE\s*\(', FLAGS),
        replacement='SIZE(',
        confidence="high",
    ),
    Rule(
        name="ARRAY_CAT",
        pattern=re.compile(r'\bARRAY_CAT\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'CONCAT(\1, \2)',
        confidence="high",
    ),
    Rule(
        name="ARRAY_COMPACT",
        pattern=re.compile(r'\bARRAY_COMPACT\s*\(([^)]+)\)', FLAGS),
        replacement=r'FILTER(\1, x -> x IS NOT NULL)',
        confidence="high",
    ),
    Rule(
        name="ARRAY_FLATTEN",
        pattern=re.compile(r'\bARRAY_FLATTEN\s*\(', FLAGS),
        replacement='FLATTEN(',
        confidence="high",
    ),
    Rule(
        name="ARRAY_TO_STRING",
        pattern=re.compile(r'\bARRAY_TO_STRING\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'ARRAY_JOIN(\1, \2)',
        confidence="high",
    ),
    Rule(
        name="ARRAY_SLICE",
        pattern=re.compile(r'\bARRAY_SLICE\s*\(([^,]+),\s*([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'SLICE(\1, \2, \3)',
        confidence="high",
    ),
    Rule(
        name="ARRAY_INTERSECTION",
        pattern=re.compile(r'\bARRAY_INTERSECTION\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'ARRAY_INTERSECT(\1, \2)',
        confidence="high",
    ),
    Rule(
        name="ARRAY_PREPEND",
        pattern=re.compile(r'\bARRAY_PREPEND\s*\(([^,]+),\s*([^)]+)\)', FLAGS),
        replacement=r'ARRAY_INSERT(\2, 0, \1)',
        confidence="medium",
    ),
    Rule(
        name="ARRAY_CONSTRUCT",
        pattern=re.compile(r'\bARRAY_CONSTRUCT\s*\(', FLAGS),
        replacement='ARRAY(',
        confidence="high",
    ),
    Rule(
        name="ARRAY_UNION_AGG",
        pattern=re.compile(r'\bARRAY_UNION_AGG\s*\(([^)]+)\)', FLAGS),
        replacement=r'ARRAY_DISTINCT(FLATTEN(COLLECT_LIST(\1)))',
        confidence="medium",
    ),
    Rule(
        name="OBJECT_CONSTRUCT",
        pattern=re.compile(r'\bOBJECT_CONSTRUCT(?:_KEEP_NULL)?\s*\(', FLAGS),
        replacement=None,   # Escalate to Claude
        confidence="low",
        note="OBJECT_CONSTRUCT requires AI conversion → NAMED_STRUCT or TO_JSON(STRUCT(...))",
    ),
    Rule(
        name="OBJECT_KEYS",
        pattern=re.compile(r'\bOBJECT_KEYS\s*\(', FLAGS),
        replacement='MAP_KEYS(',
        confidence="high",
    ),
    Rule(
        name="PARSE_JSON",
        pattern=re.compile(r'\bPARSE_JSON\s*\(', FLAGS),
        replacement=None,   # Escalate to Claude (needs schema arg)
        confidence="low",
        note="PARSE_JSON → FROM_JSON(str, schema) requires schema definition — AI needed",
    ),
    Rule(
        name="GET_PATH",
        pattern=re.compile(r'\bGET_PATH\s*\(([^,]+),\s*\'([^\']+)\'\)', FLAGS),
        replacement=r"GET_JSON_OBJECT(\1, '$.\2')",
        confidence="medium",
    ),
    Rule(
        name="IS_ARRAY",
        pattern=re.compile(r'\bIS_ARRAY\s*\(([^)]+)\)', FLAGS),
        replacement=r'(JSON_ARRAY_LENGTH(\1) IS NOT NULL)',
        confidence="medium",
    ),
    Rule(
        name="TYPEOF_fn",
        pattern=re.compile(r'\bTYPEOF\s*\(', FLAGS),
        replacement=None,
        confidence="low",
        note="TYPEOF has no direct Databricks equivalent — MANUAL_REVIEW",
    ),

    # =========================================================
    # CONVERSION FUNCTIONS
    # =========================================================
    Rule(
        name="TO_NUMBER",
        pattern=re.compile(r'\bTO_NUMBER\s*\(([^,)]+)\)', FLAGS),
        replacement=r'CAST(\1 AS DECIMAL)',
        confidence="high",
    ),
    Rule(
        name="TRY_TO_NUMBER",
        pattern=re.compile(r'\bTRY_TO_NUMBER\s*\(([^,)]+)\)', FLAGS),
        replacement=r'TRY_CAST(\1 AS DECIMAL)',
        confidence="high",
    ),
    Rule(
        name="TO_DOUBLE",
        pattern=re.compile(r'\bTO_DOUBLE\s*\(([^)]+)\)', FLAGS),
        replacement=r'CAST(\1 AS DOUBLE)',
        confidence="high",
    ),
    Rule(
        name="TRY_TO_DATE",
        pattern=re.compile(r'\bTRY_TO_DATE\s*\(', FLAGS),
        replacement='TRY_TO_TIMESTAMP(',
        confidence="medium",
    ),
    Rule(
        name="TO_BINARY",
        pattern=re.compile(r'\bTO_BINARY\s*\(([^,]+),\s*\'HEX\'\)', FLAGS),
        replacement=r'UNHEX(\1)',
        confidence="high",
    ),

    # =========================================================
    # COLON PATH NOTATION  col:key::TYPE
    # Must run AFTER all other rules to avoid double-conversion
    # =========================================================
    Rule(
        name="COLON_PATH_TYPED",
        pattern=re.compile(r'\b([a-zA-Z_]\w*)(?::[a-zA-Z_]\w*)+::\w+\b', FLAGS),
        replacement=_colon_path_to_json,
        confidence="medium",
        note="Snowflake colon-path::TYPE notation → CAST(GET_JSON_OBJECT(...))",
    ),
    Rule(
        name="COLON_PATH_PLAIN",
        pattern=re.compile(r'\b([a-zA-Z_]\w*)(?::[a-zA-Z_]\w*)+\b', FLAGS),
        replacement=_colon_path_to_json,
        confidence="medium",
        note="Snowflake colon-path notation → GET_JSON_OBJECT(...)",
    ),

    # =========================================================
    # LATERAL FLATTEN
    # =========================================================
    Rule(
        name="LATERAL_FLATTEN_INPUT",
        pattern=re.compile(
            r'LATERAL\s+FLATTEN\s*\(\s*INPUT\s*=>\s*([^,)]+)(?:[^)]*)\)',
            FLAGS,
        ),
        replacement=r'LATERAL VIEW EXPLODE(\1) _flat AS value',
        confidence="medium",
        note="LATERAL FLATTEN → LATERAL VIEW EXPLODE; adjust alias as needed",
    ),
    Rule(
        name="LATERAL_FLATTEN_PLAIN",
        pattern=re.compile(r'LATERAL\s+FLATTEN\s*\(([^)]+)\)', FLAGS),
        replacement=r'LATERAL VIEW EXPLODE(\1) _flat AS value',
        confidence="medium",
        note="LATERAL FLATTEN → LATERAL VIEW EXPLODE",
    ),
]
