"""Restricted boolean-expression evaluator for connector `index_filter` (design/06 §4).

A connector's [[objects]] config can carry e.g.
    index_filter = 'status == "open" and priority in ["high", "urgent"]'
to decide which records get indexed. This is NEVER `eval`'d: we parse to an AST and
walk a tiny whitelist — bare names resolve against the record dict, and only
comparisons / boolean ops / membership / literals are allowed. Anything else
(calls, attribute access, arithmetic, dunders) raises, so a config string can't run
arbitrary code.
"""
from __future__ import annotations

import ast
from typing import Any

_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.UnaryOp, ast.Not, ast.And, ast.Or,
    ast.Compare, ast.Name, ast.Load, ast.Constant, ast.List, ast.Tuple,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
)


class FilterError(ValueError):
    pass


def _check(node: ast.AST) -> None:
    for n in ast.walk(node):
        if not isinstance(n, _ALLOWED_NODES):
            raise FilterError(f"disallowed expression element: {type(n).__name__}")


def compile_filter(expr: str):
    """Parse + validate once; returns a callable record->bool. Raises FilterError on
    anything outside the whitelist."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise FilterError(f"bad index_filter: {e}") from e
    _check(tree)

    def predicate(record: dict) -> bool:
        return bool(_eval(tree.body, record))

    return predicate


def _eval(node: ast.AST, rec: dict) -> Any:
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, rec) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval(node.operand, rec)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, rec)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, rec)
            if not _cmp(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        return rec.get(node.id)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, rec) for e in node.elts]
    raise FilterError(f"cannot evaluate node: {type(node).__name__}")


def _cmp(op: ast.cmpop, a: Any, b: Any) -> bool:
    if isinstance(op, ast.Eq):
        return a == b
    if isinstance(op, ast.NotEq):
        return a != b
    if isinstance(op, ast.In):
        try:
            return a in b
        except TypeError:
            return False
    if isinstance(op, ast.NotIn):
        try:
            return a not in b
        except TypeError:
            return True
    # ordered comparisons; None is treated as "fails" rather than raising
    if a is None or b is None:
        return False
    if isinstance(op, ast.Lt):
        return a < b
    if isinstance(op, ast.LtE):
        return a <= b
    if isinstance(op, ast.Gt):
        return a > b
    if isinstance(op, ast.GtE):
        return a >= b
    raise FilterError(f"unsupported comparator: {type(op).__name__}")
