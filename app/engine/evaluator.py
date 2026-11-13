from __future__ import annotations

import ast
import operator
from typing import Any, Dict, Tuple

from app.schemas.workflow import FieldSpec


class RuleEvaluationError(Exception):
    pass


class InputValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


_ALLOWED_COMPARE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}

_ALLOWED_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: lambda a, b: a / b if b != 0 else 0.0,
    ast.FloorDiv: lambda a, b: a // b if b != 0 else 0,
    ast.Mod: lambda a, b: a % b if b != 0 else 0,
}

_ALLOWED_UNARY_OPS = {
    ast.Not: operator.not_,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def validate_payload_against_schema(
    payload: Dict[str, Any], schema: Dict[str, FieldSpec]
) -> None:
    errors: list[str] = []
    type_map = {
        "string": str,
        "int": int,
        "float": (int, float),
        "bool": bool,
        "list": list,
    }

    for field_name, spec in schema.items():
        if field_name not in payload or payload[field_name] is None:
            if spec.required:
                errors.append(f"Missing required field '{field_name}'")
            continue

        val = payload[field_name]
        # In Python bool is a subclass of int; guard explicitly
        if spec.type in ("int", "float") and isinstance(val, bool):
            errors.append(f"Field '{field_name}' must be {spec.type}, got bool")
            continue

        expected_type = type_map[spec.type]
        if not isinstance(val, expected_type):
            errors.append(
                f"Field '{field_name}' expected type '{spec.type}', got '{type(val).__name__}'"
            )
            continue

        if spec.min_value is not None and isinstance(val, (int, float)):
            if val < spec.min_value:
                errors.append(
                    f"Field '{field_name}' value {val} is below minimum {spec.min_value}"
                )

        if spec.max_value is not None and isinstance(val, (int, float)):
            if val > spec.max_value:
                errors.append(
                    f"Field '{field_name}' value {val} exceeds maximum {spec.max_value}"
                )

        if spec.allowed_values is not None and val not in spec.allowed_values:
            errors.append(
                f"Field '{field_name}' value '{val}' not in allowed values {spec.allowed_values}"
            )

    if errors:
        raise InputValidationError(errors)


class SafeExpressionEvaluator:
    """
    Evaluates boolean and arithmetic rule expressions safely using Python's AST module.
    Permits only literals, context variable lookups, comparisons, basic arithmetic,
    and boolean logic. Eliminates code-execution risks of eval() while recording
    exact referenced context values for audit explainability.
    """

    def evaluate(self, expression: str, context: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        referenced: Dict[str, Any] = {}
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise RuleEvaluationError(f"Invalid rule syntax '{expression}': {exc}") from exc

        result = self._eval_node(tree.body, context, referenced)
        return bool(result), referenced

    def _eval_node(
        self, node: ast.AST, context: Dict[str, Any], referenced: Dict[str, Any]
    ) -> Any:
        if isinstance(node, ast.Constant):
            return node.value

        if isinstance(node, ast.Name):
            if node.id not in context:
                raise RuleEvaluationError(f"Referenced field '{node.id}' missing in context")
            referenced[node.id] = context[node.id]
            return context[node.id]

        if isinstance(node, ast.List):
            return [self._eval_node(elt, context, referenced) for elt in node.elts]

        if isinstance(node, ast.Tuple):
            return tuple(self._eval_node(elt, context, referenced) for elt in node.elts)

        if isinstance(node, ast.Set):
            return {self._eval_node(elt, context, referenced) for elt in node.elts}

        if isinstance(node, ast.BinOp):
            op_fn = _ALLOWED_BIN_OPS.get(type(node.op))
            if not op_fn:
                raise RuleEvaluationError(f"Unsupported binary operator: {type(node.op).__name__}")
            left = self._eval_node(node.left, context, referenced)
            right = self._eval_node(node.right, context, referenced)
            return op_fn(left, right)

        if isinstance(node, ast.UnaryOp):
            op_fn = _ALLOWED_UNARY_OPS.get(type(node.op))
            if not op_fn:
                raise RuleEvaluationError(f"Unsupported unary operator: {type(node.op).__name__}")
            return op_fn(self._eval_node(node.operand, context, referenced))

        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                for val_node in node.values:
                    if not self._eval_node(val_node, context, referenced):
                        return False
                return True
            if isinstance(node.op, ast.Or):
                for val_node in node.values:
                    if self._eval_node(val_node, context, referenced):
                        return True
                return False
            raise RuleEvaluationError(f"Unsupported boolean operator: {type(node.op).__name__}")

        if isinstance(node, ast.Compare):
            left = self._eval_node(node.left, context, referenced)
            for op, comparator in zip(node.ops, node.comparators):
                op_fn = _ALLOWED_COMPARE_OPS.get(type(op))
                if not op_fn:
                    raise RuleEvaluationError(f"Unsupported comparison: {type(op).__name__}")
                right = self._eval_node(comparator, context, referenced)
                if not op_fn(left, right):
                    return False
                left = right
            return True

        raise RuleEvaluationError(f"Disallowed AST construct in rule: {type(node).__name__}")
