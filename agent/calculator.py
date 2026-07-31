"""
Calculator Tool

Safely evaluates simple mathematical
expressions.
"""
import ast
import math
import operator

class CalculatorTool:
    """
    Basic calculator.
    """

    def calculate(
        self,
        expression: str
    ) -> str:
        try:
            result = self._evaluate(ast.parse(expression, mode="eval").body)
            return str(result)
        
        except Exception as error:
            return f"Calculation Error: {error}"

    def _evaluate(self, node):
        binary_operators = {
            ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
            ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
        }
        unary_operators = {ast.UAdd: operator.pos, ast.USub: operator.neg}

        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in binary_operators:
            return binary_operators[type(node.op)](
                self._evaluate(node.left), self._evaluate(node.right)
            )
        if isinstance(node, ast.UnaryOp) and type(node.op) in unary_operators:
            return unary_operators[type(node.op)](self._evaluate(node.operand))
        if isinstance(node, ast.Name) and node.id in {"pi", "e"}:
            return getattr(math, node.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            functions = {"abs": abs, "round": round, "min": min, "max": max,
                         "pow": pow, "sqrt": math.sqrt, "ceil": math.ceil,
                         "floor": math.floor}
            if node.func.id in functions:
                return functions[node.func.id](*[self._evaluate(arg) for arg in node.args])
        raise ValueError("Only basic mathematical expressions are supported")

calculator = CalculatorTool()
