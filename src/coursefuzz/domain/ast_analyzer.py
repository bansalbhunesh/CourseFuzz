from __future__ import annotations

import ast
from typing import NamedTuple


class ASTStructuralInvariants(NamedTuple):
    """Static AST analysis findings extracted from reference code."""

    boundary_constants: tuple[int, ...]
    comparison_operators: tuple[str, ...]
    has_loops: bool
    branch_count: int


class ASTAnalyzer(ast.NodeVisitor):
    """Static AST visitor to extract control-flow predicates and boundary constants."""

    def __init__(self) -> None:
        self.constants: set[int] = set()
        self.comp_ops: set[str] = set()
        self.has_loops: bool = False
        self.branch_count: int = 0

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, int) and not isinstance(node.value, bool):
            self.constants.add(node.value)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        for op in node.ops:
            self.comp_ops.add(type(op).__name__)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self.branch_count += 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.branch_count += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.has_loops = True
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.has_loops = True
        self.generic_visit(node)


def analyze_source_ast(source_code: str) -> ASTStructuralInvariants:
    """Analyze Python source code and return static AST invariants."""
    try:
        tree = ast.parse(source_code)
        visitor = ASTAnalyzer()
        visitor.visit(tree)
        return ASTStructuralInvariants(
            boundary_constants=tuple(sorted(visitor.constants)),
            comparison_operators=tuple(sorted(visitor.comp_ops)),
            has_loops=visitor.has_loops,
            branch_count=visitor.branch_count,
        )
    except SyntaxError:
        return ASTStructuralInvariants(
            boundary_constants=(),
            comparison_operators=(),
            has_loops=False,
            branch_count=0,
        )
