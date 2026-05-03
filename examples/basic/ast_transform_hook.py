"""Plug user logic into paker's pack pipeline via ``ast_transform`` / ``code_transform``.

paker itself doesn't ship an obfuscation pipeline. The hooks are the contract:

- ``ast_transform(tree, module_name) -> ast.AST``
    Runs after ``ast.parse`` and before ``compile``. Rewrite the tree, inject
    docstring decoys, strip comments / asserts — whatever you want.

- ``code_transform(code_obj, module_name) -> types.CodeType``
    Runs after ``compile(..., optimize=2)`` and before ``marshal.dumps``.
    Useful for bytecode-level transforms (e.g. custom metadata scrubbing).

This example shows both: an AST pass that strips every docstring and a code
pass that blanks ``co_name`` on nested code objects. Run against the bundled
``testpackage`` fixture.
"""

from __future__ import annotations

import ast
import os
import sys
import types

import paker


class _DocstringStripper(ast.NodeTransformer):
    """Remove the leading string expression from every module / class / def."""

    def visit_Module(self, node):
        self.generic_visit(node)
        _drop_leading_docstring(node.body)
        return node

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        _drop_leading_docstring(node.body)
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.generic_visit(node)
        _drop_leading_docstring(node.body)
        return node


def _drop_leading_docstring(body: list[ast.stmt]) -> None:
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body.pop(0)
    if not body:
        body.append(ast.Pass())


def strip_docstrings(tree: ast.AST, module_name: str) -> ast.AST:
    """An ``ast_transform`` — wipes every docstring in the module."""
    return _DocstringStripper().visit(tree)


def blank_nested_qualnames(
    code: types.CodeType,
    module_name: str,
) -> types.CodeType:
    """A ``code_transform`` — clear ``co_qualname`` on nested code objects.

    Complements paker's default ``strip_metadata=True`` (which also clears
    ``co_filename`` and top-level qualnames). Shown here as an illustration;
    paker ships equivalent scrubbing out of the box.
    """

    def scrub(co: types.CodeType, top: bool) -> types.CodeType:
        new_consts = tuple(
            scrub(c, top=False) if isinstance(c, types.CodeType) else c
            for c in co.co_consts
        )
        repl = {"co_consts": new_consts}
        if not top and hasattr(co, "co_qualname"):
            repl["co_qualname"] = ""
        return co.replace(**repl)

    return scrub(code, top=True)


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(
        os.path.dirname(__file__), "..", "..", "tests", "tests_data",
    ))
    try:
        for name in list(sys.modules):
            if name.startswith("testpackage"):
                del sys.modules[name]

        # Pack with the hooks applied.
        bundle = paker.dumps(
            "testpackage",
            ast_transform=strip_docstrings,
            code_transform=blank_nested_qualnames,
        )

        # Load and use — the stripped docstrings don't break execution.
        for name in list(sys.modules):
            if name.startswith("testpackage"):
                del sys.modules[name]

        with paker.loads(bundle):
            import testpackage
            assert testpackage.Fibonacci.fib(5) == [0, 1, 1, 2, 3]
            print("loaded OK; docstrings:", testpackage.Fibonacci.__doc__)
            print(
                "fib().__doc__:",
                testpackage.Fibonacci.fib.__doc__,
            )
    finally:
        sys.path.pop(0)
