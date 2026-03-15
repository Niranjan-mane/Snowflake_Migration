"""
Split a SQL file containing multiple statements into individual statements.
Handles $$ delimiters, single/double quotes, and standard semicolons.
"""
from __future__ import annotations

import re


def split_statements(sql: str) -> list[str]:
    """
    Split SQL text into individual statements.
    Respects:
      - $$ ... $$ blocks (stored procedure bodies)
      - Single-quoted strings
      - Double-quoted identifiers
      - Line comments (--)
      - Block comments (/* */)
    """
    statements: list[str] = []
    current: list[str] = []
    i = 0
    length = len(sql)
    in_dollar_dollar = False
    in_single_quote = False
    in_double_quote = False
    in_line_comment = False
    in_block_comment = False

    while i < length:
        ch = sql[i]
        remaining = sql[i:]

        # --- End of line comment ---
        if in_line_comment:
            current.append(ch)
            if ch == '\n':
                in_line_comment = False
            i += 1
            continue

        # --- End of block comment ---
        if in_block_comment:
            current.append(ch)
            if remaining.startswith('*/'):
                current.append(sql[i + 1])
                i += 2
                in_block_comment = False
            else:
                i += 1
            continue

        # --- Inside $$ block ---
        if in_dollar_dollar:
            current.append(ch)
            if remaining.startswith('$$'):
                current.append(sql[i + 1])
                i += 2
                in_dollar_dollar = False
            else:
                i += 1
            continue

        # --- Inside single-quoted string ---
        if in_single_quote:
            current.append(ch)
            if ch == "'" and (i + 1 >= length or sql[i + 1] != "'"):
                in_single_quote = False
            elif ch == "'" and i + 1 < length and sql[i + 1] == "'":
                # Escaped quote ''
                current.append(sql[i + 1])
                i += 2
                continue
            i += 1
            continue

        # --- Inside double-quoted identifier ---
        if in_double_quote:
            current.append(ch)
            if ch == '"':
                in_double_quote = False
            i += 1
            continue

        # --- Start of line comment ---
        if remaining.startswith('--'):
            in_line_comment = True
            current.append(ch)
            i += 1
            continue

        # --- Start of block comment ---
        if remaining.startswith('/*'):
            in_block_comment = True
            current.append(ch)
            i += 1
            continue

        # --- Start of $$ delimiter ---
        if remaining.startswith('$$'):
            in_dollar_dollar = True
            current.append('$$')
            i += 2
            continue

        # --- Start of single quote ---
        if ch == "'":
            in_single_quote = True
            current.append(ch)
            i += 1
            continue

        # --- Start of double quote ---
        if ch == '"':
            in_double_quote = True
            current.append(ch)
            i += 1
            continue

        # --- Semicolon: statement boundary ---
        if ch == ';':
            current.append(ch)
            stmt = ''.join(current).strip()
            if stmt and stmt != ';':
                statements.append(stmt)
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    # Flush remaining content (statement without trailing semicolon)
    remainder = ''.join(current).strip()
    if remainder:
        statements.append(remainder)

    return [s for s in statements if _is_meaningful(s)]


def _is_meaningful(sql: str) -> bool:
    """Return True if the statement has actual SQL content (not just whitespace/comments)."""
    stripped = re.sub(r'--[^\n]*', '', sql)           # remove line comments
    stripped = re.sub(r'/\*.*?\*/', '', stripped, flags=re.DOTALL)  # block comments
    return bool(stripped.strip())
