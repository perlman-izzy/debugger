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


def _classes(tree: ast.Module) -> dict[str, ast.ClassDef]:
    return {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}


def _methods(cls: ast.ClassDef) -> dict[str, ast.AST]:
    return {
        node.name: node
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _same_nodes(left: list[ast.AST], right: list[ast.AST]) -> bool:
    return [ast.dump(x, include_attributes=False) for x in left] == [
        ast.dump(x, include_attributes=False) for x in right
    ]


def _preserves_existing_test_structure(original_tree: ast.Module, replacement_tree: ast.Module) -> tuple[bool, str]:
    original_classes = _classes(original_tree)
    replacement_classes = _classes(replacement_tree)

    for name, old_cls in original_classes.items():
        new_cls = replacement_classes.get(name)
        if new_cls is None:
            return False, f"existing class removed: {name}"
        if not _same_nodes(old_cls.bases, new_cls.bases):
            return False, f"existing class bases changed: {name}"
        if not _same_nodes(old_cls.decorator_list, new_cls.decorator_list):
            return False, f"existing class decorators changed: {name}"
        if [ast.dump(x, include_attributes=False) for x in old_cls.keywords] != [
            ast.dump(x, include_attributes=False) for x in new_cls.keywords
        ]:
            return False, f"existing class keywords changed: {name}"

        old_methods = _methods(old_cls)
        new_methods = _methods(new_cls)
        for method_name, old_method in old_methods.items():
            new_method = new_methods.get(method_name)
            if new_method is None:
                return False, f"existing method removed: {name}.{method_name}"
            if ast.dump(old_method, include_attributes=False) != ast.dump(new_method, include_attributes=False):
                return False, f"existing method changed: {name}.{method_name}"

        old_nonmethods = [
            node for node in old_cls.body if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        new_nonmethods = [
            node for node in new_cls.body if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if not _same_nodes(old_nonmethods, new_nonmethods):
            return False, f"existing class non-method statements changed: {name}"

    return True, "OK"


def validate_proposal(path: str, replacement: str, original: str) -> tuple[bool, str]:
    allowed = _ALLOWED_IMPORTS.get(path)
    if allowed is None:
        return False, f"path not guard-approved: {path}"

    try:
        tree = ast.parse(replacement, filename=path)
        original_tree = ast.parse(original, filename=f"original:{path}") if original else ast.Module(body=[], type_ignores=[])
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
        ok, reason = _preserves_existing_test_structure(original_tree, tree)
        if not ok:
            return False, reason
        old_tests = _test_names(original)
        new_tests = _test_names(replacement)
        added = sorted(new_tests - old_tests)
        if not added:
            return False, "test change must add at least one new test"

    return True, "OK"
