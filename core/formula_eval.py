"""Evaluate numeric recipe formulas without coercing invalid inputs to zero."""
import ast
import operator

from core.numeric_validation import finite_reading


def evaluate_formula(expression, values):
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
              ast.Mod: operator.mod, ast.Pow: operator.pow}
    functions = {'abs': abs, 'round': round, 'min': min, 'max': max}
    tree = ast.parse(expression, mode='eval')
    if sum(1 for _ in ast.walk(tree)) > 200:
        raise ValueError('公式过于复杂')

    def visit(node):
        if isinstance(node, ast.Constant):
            return finite_reading(node.value)
        if isinstance(node, ast.Name):
            key = node.id
            if key not in values and key.lower().startswith('step') and key[4:].isdigit():
                key = key.upper()
            if key not in values:
                raise ValueError(f'变量不存在：{key}')
            return finite_reading(values[key])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return finite_reading(visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1))
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 1000:
                raise ValueError('公式指数超出范围')
            return finite_reading(binary[type(node.op)](left, right))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions and not node.keywords:
            args = [visit(arg) for arg in node.args]
            if node.func.id == 'round' and len(args) == 2:
                if not args[1].is_integer() or abs(args[1]) > 308:
                    raise ValueError('round 位数无效')
                args[1] = int(args[1])
            return finite_reading(functions[node.func.id](*args))
        raise ValueError('公式仅支持数值、变量、算术及 abs/round/min/max')

    return visit(tree.body)
