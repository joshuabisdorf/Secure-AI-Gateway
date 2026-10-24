from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_LINE_LENGTH = 80

_TEXT_SUFFIXES = {
    ".json",
    ".md",
    ".py",
    ".sh",
    ".sql",
    ".tf",
    ".tfvars",
    ".toml",
    ".yaml",
    ".yml",
}
_TEXT_NAMES = {
    ".client.env.example",
    ".dockerignore",
    ".editorconfig",
    ".env.example",
    ".gitignore",
    "Dockerfile",
    "Makefile",
    "NOTICE",
    "SECURITY.md",
}
_EXCLUDED_PATHS = {
    "LICENSE",
    "requirements/release.lock",
}
_EXCLUDED_PREFIXES = ("evals/datasets/",)
_URL_RE = re.compile(r"https?://\S+")
_ACTION_RE = re.compile(
    r"^\s*uses:\s*[^\s]+@[0-9a-f]{40}(?:\s+#.*)?$"
)
_TF_BLOCK_HEADER_RE = re.compile(
    r'^\s*(?:resource|data)\s+"[^"]+"\s+"[^"]+"\s+\{$'
)
_LONG_ATOM_RE = re.compile(r"\S{81,}")
_SNAKE_CASE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_CLASS_NAME_RE = re.compile(r"^_?[A-Z][A-Za-z0-9]*$")
_DOCSTRING_FIELD_ORDER = {
    "param": 0,
    "returns": 1,
    "raises": 2,
    "note": 3,
}
_LEGACY_DOCSTRING_HEADING_RE = re.compile(
    r"^\s*(?:Args|Returns|Raises|Notes|Requires|Modifies|Effects|"
    r"Inputs|Outputs):\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class Violation:
    """Represent one project style violation.

    :param path: Repository-relative file path. line: One-based line number, or
        zero for file-level errors. rule: Stable rule identifier. message:
        Human-readable diagnostic text.
    :returns: An immutable violation value.

    .. note::

        - Precondition: path identifies a repository-relative file.
    """

    path: str
    line: int
    rule: str
    message: str


def _run_git(
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a read-only Git command from the repository root.

    :param args: Git arguments after the executable name. check: Whether
        non-zero exit status raises an exception.
    :returns: Completed subprocess result with text output.

    .. note::

        - Precondition: Git is installed and ROOT is inside a Git working tree.
    """
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )



def _tracked_files() -> list[Path]:
    """Return tracked text candidates in deterministic order.

    :returns: Sorted repository-relative paths eligible for style checks.

    .. note::

        - Precondition: The repository index is readable by Git.
    """
    output = _run_git("ls-files").stdout
    candidates: list[Path] = []
    for raw_path in output.splitlines():
        if not raw_path:
            continue
        if raw_path in _EXCLUDED_PATHS:
            continue
        if raw_path.startswith(_EXCLUDED_PREFIXES):
            continue
        path = Path(raw_path)
        if path.name in _TEXT_NAMES or path.suffix in _TEXT_SUFFIXES:
            candidates.append(path)
    return sorted(candidates)



def _line_length_exception(path: Path, line: str) -> bool:
    """Return whether an overlong line is indivisible by project policy.

    :param path: Repository-relative path containing the line. line: Candidate
        overlong line.
    :returns: True only for narrow atomic forms that should not be split.

    .. note::

        - Precondition: line does not include its trailing newline.
    """
    stripped = line.strip()
    if not stripped:
        return False
    if path.suffix == ".md" and stripped.startswith("|"):
        return True
    if (
        path.suffix == ".tf"
        and _TF_BLOCK_HEADER_RE.fullmatch(line)
    ):
        return True
    if _ACTION_RE.fullmatch(line):
        return True
    if _URL_RE.search(line):
        return True
    if _LONG_ATOM_RE.search(line):
        return True
    return False


def _check_text_file(path: Path) -> list[Violation]:
    """Check universal text-file invariants.

    :param path: Repository-relative file path.
    :returns: Violations for universal text style rules.

    .. note::

        - Precondition: path exists under ROOT and contains tracked text.
    """
    full_path = ROOT / path
    violations: list[Violation] = []
    data = full_path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [
            Violation(str(path), 0, "TXT001", "file is not UTF-8")
        ]

    if data and not data.endswith(b"\n"):
        violations.append(
            Violation(
                str(path),
                0,
                "TXT002",
                "missing final newline",
            )
        )

    for line_number, line in enumerate(text.splitlines(), start=1):
        if line.rstrip(" \t") != line:
            violations.append(
                Violation(
                    str(path),
                    line_number,
                    "TXT003",
                    "trailing whitespace",
                )
            )
        if "\t" in line and path.name != "Makefile":
            violations.append(
                Violation(
                    str(path),
                    line_number,
                    "TXT004",
                    "tab character",
                )
            )
        if len(line) > MAX_LINE_LENGTH and not _line_length_exception(
            path,
            line,
        ):
            violations.append(
                Violation(
                    str(path),
                    line_number,
                    "TXT005",
                    f"line length {len(line)} exceeds "
                    f"{MAX_LINE_LENGTH}",
                )
            )
    return violations


def _is_runtime_python(path: Path) -> bool:
    """Return whether a Python file is maintained project code.

    :param path: Repository-relative path.
    :returns: True for app modules and Python utilities under scripts.

    .. note::

        - Precondition: path is repository-relative.
    """
    return (
        path.suffix == ".py"
        and path.parts[0] in {"app", "scripts"}
        and path.name != "style_check.py"
    )


def _is_dunder(name: str) -> bool:
    """Return whether a Python name is a language-protocol dunder.

    :param name: Identifier to inspect.
    :returns: True when name starts and ends with two underscores.

    .. note::

        - Precondition: name is a Python identifier.
    """
    return name.startswith("__") and name.endswith("__")


def _docstring_fields(node: ast.AST) -> list[str]:
    """Collect Sphinx field-list entries and note directives in source order.

    :param node: Function or method AST node.
    :returns: Field names in their order of appearance.
    """
    docstring = ast.get_docstring(node, clean=False) or ""
    return [
        match.group(1) or "note"
        for match in re.finditer(
            r"^\s*:(param|returns|raises)(?:\s+[^:]+)?:"
            r"|^\s*\.\. note::\s*$",
            docstring,
            flags=re.MULTILINE,
        )
    ]


def _requires_docstring(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Return whether a function needs a complete docstring.

    :param node: Parsed function definition.
    :returns: True for non-trivial project functions.

    .. note::

        - Precondition: node is a parsed function definition.
    """
    if _is_dunder(node.name):
        return False
    if (
        len(node.body) == 1
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and node.body[0].value.value is Ellipsis
    ):
        return False
    if node.name.startswith("_") and len(node.body) <= 3:
        return False
    return True


def _check_docstring(
    path: Path,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[Violation]:
    """Check a function's native Sphinx documentation contract.

    :param path: Repository-relative Python path.
    :param node: Function or method syntax node.
    :returns: Docstring and field-order violations.
    """
    docstring = ast.get_docstring(node) or ""
    if not docstring.strip():
        return [
            Violation(
                str(path),
                node.lineno,
                "PY006",
                f"{node.name} requires a Sphinx-style docstring",
            )
        ]

    if _LEGACY_DOCSTRING_HEADING_RE.search(docstring):
        return [
            Violation(
                str(path),
                node.lineno,
                "PY011",
                f"{node.name} uses a legacy docstring heading",
            )
        ]

    fields = _docstring_fields(node)
    positions = [_DOCSTRING_FIELD_ORDER[name] for name in fields]
    if positions != sorted(positions):
        return [
            Violation(
                str(path),
                node.lineno,
                "PY007",
                f"{node.name} has Sphinx fields out of order",
            )
        ]
    if fields.count("returns") > 1:
        return [
            Violation(
                str(path),
                node.lineno,
                "PY007",
                f"{node.name} has duplicate return fields",
            )
        ]
    return []


def _check_python(path: Path) -> list[Violation]:
    """Check Python-specific structural rules without external linters.

    :param path: Repository-relative Python path.
    :returns: Python-specific style violations.

    .. note::

        - Precondition: path identifies UTF-8 Python source under ROOT.
    """
    source = (ROOT / path).read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [
            Violation(
                str(path),
                exc.lineno or 0,
                "PY001",
                f"syntax error: {exc.msg}",
            )
        ]

    violations: list[Violation] = []
    module_name = path.stem
    if module_name != "__init__" and not _SNAKE_CASE_RE.fullmatch(
        module_name
    ):
        violations.append(
            Violation(
                str(path),
                1,
                "PY008",
                "Python module name must be snake_case",
            )
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(
            alias.name == "*" for alias in node.names
        ):
            violations.append(
                Violation(
                    str(path),
                    node.lineno,
                    "PY002",
                    "wildcard import",
                )
            )
        if isinstance(node, ast.Import) and len(node.names) > 1:
            violations.append(
                Violation(
                    str(path),
                    node.lineno,
                    "PY003",
                    "multiple imports in one statement",
                )
            )
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            violations.append(
                Violation(
                    str(path),
                    node.lineno,
                    "PY004",
                    "bare except",
                )
            )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not _is_dunder(node.name) and not _SNAKE_CASE_RE.fullmatch(
                node.name
            ):
                violations.append(
                    Violation(
                        str(path),
                        node.lineno,
                        "PY009",
                        f"function name is not snake_case: {node.name}",
                    )
                )
            defaults = [*node.args.defaults, *node.args.kw_defaults]
            for default in defaults:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    violations.append(
                        Violation(
                            str(path),
                            node.lineno,
                            "PY005",
                            f"mutable default in {node.name}",
                        )
                    )
            if _is_runtime_python(path) and _requires_docstring(node):
                violations.extend(_check_docstring(path, node))
        if isinstance(node, ast.ClassDef):
            if not _CLASS_NAME_RE.fullmatch(node.name):
                violations.append(
                    Violation(
                        str(path),
                        node.lineno,
                        "PY010",
                        f"class name is not CapWords: {node.name}",
                    )
                )
    return violations


def _check_shell(path: Path) -> list[Violation]:
    """Check repository shell entrypoint conventions.

    :param path: Repository-relative shell script path.
    :returns: Shell shebang and strict-mode violations.

    .. note::

        - Precondition: path identifies a UTF-8 shell script under ROOT.
    """
    text = (ROOT / path).read_text(encoding="utf-8")
    lines = text.splitlines()
    violations: list[Violation] = []
    if not lines or lines[0] != "#!/usr/bin/env bash":
        violations.append(
            Violation(
                str(path),
                1,
                "SH001",
                "shell script must use env bash shebang",
            )
        )
    early_lines = lines[1:12]
    if not any(line.strip() == "set -euo pipefail" for line in early_lines):
        violations.append(
            Violation(
                str(path),
                2,
                "SH002",
                "shell script must enable set -euo pipefail",
            )
        )
    return violations


def collect_violations() -> tuple[list[Violation], int]:
    """Collect machine-checkable project style violations.

    :returns: Violations and the checked-file count.

    .. note::

        - Precondition: Repository files are readable and Git is available.
    """
    violations: list[Violation] = []
    checked_files = 0
    for path in _tracked_files():
        checked_files += 1
        violations.extend(_check_text_file(path))
        if path.suffix == ".py":
            violations.extend(_check_python(path))
        if path.suffix == ".sh":
            violations.extend(_check_shell(path))

    violations.sort(
        key=lambda item: (
            item.path,
            item.line,
            item.rule,
            item.message,
        )
    )
    return violations, checked_files

def main() -> int:
    """Run the whole-repository style gate and print diagnostics.

    :returns: Process status 0 when clean, otherwise 1.

    .. note::

        - Precondition: The command runs from a Git checkout of this project.
    """
    try:
        violations, checked_files = collect_violations()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"style_check=ERROR detail={exc}")
        return 2

    for violation in violations:
        location = violation.path
        if violation.line:
            location = f"{location}:{violation.line}"
        print(
            f"{location}: {violation.rule} "
            f"{violation.message}"
        )

    if violations:
        print(
            "style_check=FAIL mode=whole-tree "
            f"violations={len(violations)} "
            f"checked_files={checked_files}"
        )
        return 1

    print(
        "style_check=PASS mode=whole-tree violations=0 "
        f"checked_files={checked_files}"
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
