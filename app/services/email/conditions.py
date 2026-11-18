"""Condition evaluation for admin automations.

Stored as JSON, never code:
  {"match": "all" | "any", "rules": [{"field": "facts.resume_count", "op": "eq", "value": 0}, ...]}

`field` is a dotted path into {"event": <payload>, "facts": <derived facts>}.
Unknown fields resolve to None. No eval, no SQL.
"""
from __future__ import annotations

from typing import Any

OPS = ("eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "is_true", "is_false", "exists", "not_exists")


class ConditionError(ValueError):
    pass


def _get(data: dict, path: str) -> Any:
    cur: Any = data
    for part in (path or "").split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _cmp(actual: Any, op: str, expected: Any) -> bool:
    if op == "exists":
        return actual is not None
    if op == "not_exists":
        return actual is None
    if op == "is_true":
        return bool(actual) is True
    if op == "is_false":
        return not bool(actual)
    if op in ("eq", "neq"):
        a, e = actual, expected
        if _num(a) is not None and _num(e) is not None and not isinstance(a, bool):
            a, e = _num(a), _num(e)
        elif isinstance(a, str) or isinstance(e, str):
            a, e = str(a).lower() if a is not None else None, str(e).lower() if e is not None else None
        return (a == e) if op == "eq" else (a != e)
    if op in ("gt", "gte", "lt", "lte"):
        a, e = _num(actual), _num(expected)
        if a is None or e is None:
            return False
        return {"gt": a > e, "gte": a >= e, "lt": a < e, "lte": a <= e}[op]
    if op in ("in", "not_in"):
        items = expected if isinstance(expected, list) else [s.strip() for s in str(expected or "").split(",") if s.strip()]
        hit = str(actual).lower() in {str(i).lower() for i in items}
        return hit if op == "in" else not hit
    if op == "contains":
        if isinstance(actual, list):
            return str(expected).lower() in {str(i).lower() for i in actual}
        return str(expected).lower() in str(actual or "").lower()
    raise ConditionError(f"unknown operator {op!r}")


def evaluate(cond: dict | None, data: dict) -> bool:
    if not cond or not cond.get("rules"):
        return True
    results = [_cmp(_get(data, r.get("field", "")), r.get("op", "eq"), r.get("value")) for r in cond["rules"]]
    return any(results) if cond.get("match") == "any" else all(results)


def validate(cond: dict | None) -> None:
    if not cond:
        return
    if cond.get("match", "all") not in ("all", "any"):
        raise ConditionError("match must be 'all' or 'any'")
    for r in cond.get("rules") or []:
        if not str(r.get("field", "")).startswith(("event.", "facts.")):
            raise ConditionError("field must start with 'event.' or 'facts.'")
        if r.get("op") not in OPS:
            raise ConditionError(f"unknown operator {r.get('op')!r}")
