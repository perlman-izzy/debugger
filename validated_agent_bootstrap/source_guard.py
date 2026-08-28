from __future__ import annotations

import ast
import re

_ALLOWED_IMPORTS = {
    "epistemic.py": {"dataclasses", "enum", "typing"},
    "test_epistemic.py": {"unittest", "epistemic"},
}
_FORBIDDEN_CALL_NAMES = {
    "__import__",
    "eval",
    "exec",
    "compile",
    "open",
    "input",
    "breakpoint",
}
_FORBIDDEN_ATTRIBUTE_CALLS = {
    "system",
    "popen",
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",
    "urlopen",
    "request",
    "connect",
    "socket",
    "write_text",
    "write_bytes",
    "unlink",
    "remove",
    "rename",
    "replace",
    "mkdir",
    "rmdir",
    "chmod",
    "touch",
}


def _test_names(text: str) -> set[str]:
    return set(re.findall(r"^\s*def\s+(test_[A-Za-z0-9_]+)\s*\(", text, flags=re.MULTILINE))


def validate_proposal(path: str, replacement: str, original: str) -> tuple[bool, str]:
    allowed = _ALLOWED_IMPORTS.get(path)
    if allowed is None:
        return False, f"path not guard-approved: {path}"

    try:
        tree = ast.parse(replacement, filename=path)
    except SyntaxError as exc:
        return False, f"syntax error: {exc}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root not in allowed:
                    return False, f"import not allowed: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            if root not in allowed:
                return False, f"import-from not allowed: {module}"
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALL_NAMES:
                return False, f"call not allowed: {node.func.id}"
            if isinstance(node.func, ast.Attribute) and node.func.attr in _FORBIDDEN_ATTRIBUTE_CALLS:
                return False, f"attribute call not allowed: {node.func.attr}"

    if path == "test_epistemic.py":
        # Worker-authored tests are append-only. Existing tests/helpers cannot be rewritten.
        if not replacement.startswith(original):
            return False, "test changes must be append-only"
        old_tests = _test_names(original)
        new_tests = _test_names(replacement)
        added = sorted(new_tests - old_tests)
        if not added:
            return False, "append-only test change must add at least one new test"

    return True, "OK"
