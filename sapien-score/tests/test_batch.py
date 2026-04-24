"""Contract test: batch's ctx.invoke(scan, …) must pass every scan param.

The batch command explicitly enumerates kwargs so forgotten options can't
silently slip through (rather than relying on Click's default-merging).
This only works if the kwarg set stays synchronized with scan()'s Click
parameters — previously it drifted by 17+ options, which meant most
batch runs silently ignored --mode / --publish / --skip-invalid /
--layer2-threshold / tracing / replay / etc.

This test scans ``batch.py`` source for the ``ctx.invoke(scan, ...)``
call and extracts its kwarg names, then compares against scan's Click
parameter list. Any drift fails the test.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.commands import batch as batch_module
from sapien_score.commands.scan import scan as scan_command


def _scan_param_names() -> set[str]:
    """Return the set of Python parameter names scan() expects.

    Click rewrites ``--foo-bar`` into the kwarg ``foo_bar`` (or honors the
    explicit second positional arg when given). We use ``scan.callback``
    to reach the underlying function and read its signature.
    """
    sig = inspect.signature(scan_command.callback)
    # Drop click's own parameter if any; keep everything the callable expects.
    return {name for name in sig.parameters}


def _batch_invoke_kwargs() -> set[str]:
    """Parse batch.py and return the kwarg names passed to ctx.invoke(scan, …).

    AST-level rather than runtime: we want to check what the SOURCE
    passes, not what a mocked invocation gets — a test that runs the
    command would see whatever Click back-fills, which is exactly the
    behaviour we're trying to rule out.
    """
    source = Path(batch_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Look for ``ctx.invoke(scan, …)`` — attribute call on something
        # named "ctx" with method "invoke" and first positional arg
        # referencing a name we expect.
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "invoke"
            and isinstance(func.value, ast.Name)
            and func.value.id == "ctx"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "scan"
        ):
            return {kw.arg for kw in node.keywords if kw.arg is not None}
    raise AssertionError(
        "Could not find a `ctx.invoke(scan, ...)` call in batch.py — "
        "has the batch command been restructured?"
    )


class TestBatchScanParamSync:
    """batch.ctx.invoke(scan, ...) must pass every parameter scan expects."""

    def test_all_scan_params_covered(self):
        scan_params = _scan_param_names()
        batch_kwargs = _batch_invoke_kwargs()

        missing = scan_params - batch_kwargs
        assert not missing, (
            f"batch.py is not passing these scan params: {sorted(missing)}. "
            "When scan gains a new option, update batch.py's ctx.invoke(scan, ...) "
            "call with an explicit default."
        )

    def test_no_extra_kwargs_in_batch(self):
        """If batch passes kwargs scan doesn't accept, Click raises TypeError."""
        scan_params = _scan_param_names()
        batch_kwargs = _batch_invoke_kwargs()

        extra = batch_kwargs - scan_params
        assert not extra, (
            f"batch.py passes kwargs scan doesn't accept: {sorted(extra)}. "
            "Remove them or align with scan's parameter list."
        )
