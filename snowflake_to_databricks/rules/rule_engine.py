"""
Rule engine: applies ordered regex/callable transformation rules to SQL text
and tracks every change made.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional, Union


# ---------------------------------------------------------------------------
# Rule definition
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    """A single transformation rule."""
    name: str
    pattern: re.Pattern
    replacement: Union[str, Callable, None]   # None = escalate to AI
    confidence: str = "high"                  # high | medium | low
    note: str = ""
    flags: int = re.IGNORECASE | re.MULTILINE


@dataclass
class AppliedChange:
    rule_name: str
    original: str
    replacement: str
    line_number: Optional[int] = None
    confidence: str = "high"
    note: str = ""


@dataclass
class RuleEngineResult:
    sql: str
    changes: list[AppliedChange] = field(default_factory=list)
    escalate_to_ai: bool = False
    escalation_reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class RuleEngine:
    def __init__(self, rules: list[Rule]):
        self._rules = rules

    def apply(self, sql: str) -> RuleEngineResult:
        current = sql
        changes: list[AppliedChange] = []
        escalate = False
        escalation_reasons: list[str] = []

        for rule in self._rules:
            if rule.replacement is None:
                # Check if pattern matches — if yes, flag for AI escalation
                if rule.pattern.search(current):
                    escalate = True
                    escalation_reasons.append(rule.note or rule.name)
                continue

            new_sql, matched_changes = self._apply_rule(rule, current)
            if matched_changes:
                changes.extend(matched_changes)
                current = new_sql

        return RuleEngineResult(
            sql=current,
            changes=changes,
            escalate_to_ai=escalate,
            escalation_reasons=escalation_reasons,
        )

    def _apply_rule(self, rule: Rule, sql: str) -> tuple[str, list[AppliedChange]]:
        changes: list[AppliedChange] = []

        if callable(rule.replacement):
            # Callable replacement: call function with match object
            def replace_fn(m: re.Match) -> str:
                result = rule.replacement(m)
                line_num = sql[:m.start()].count('\n') + 1
                changes.append(AppliedChange(
                    rule_name=rule.name,
                    original=m.group(0),
                    replacement=result,
                    line_number=line_num,
                    confidence=rule.confidence,
                    note=rule.note,
                ))
                return result
            new_sql = rule.pattern.sub(replace_fn, sql)
        else:
            # String replacement: track each match
            positions: list[tuple[int, int, str]] = []
            for m in rule.pattern.finditer(sql):
                positions.append((m.start(), m.end(), m.group(0)))

            if not positions:
                return sql, []

            # Apply substitution
            new_sql = rule.pattern.sub(rule.replacement, sql)

            for start, _, original in positions:
                line_num = sql[:start].count('\n') + 1
                changes.append(AppliedChange(
                    rule_name=rule.name,
                    original=original,
                    replacement=rule.replacement,
                    line_number=line_num,
                    confidence=rule.confidence,
                    note=rule.note,
                ))

        return new_sql, changes
