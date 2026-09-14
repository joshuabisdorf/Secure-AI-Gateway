from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_LINE_LENGTH = 80
ALLOW_NEXT_LINE = "# sag-style: allow-next-line=unsplittable"
TEXT_SUFFIXES = {
    ".cfg",
    ".env",
    ".example",
    ".ini",
    ".json",
    ".lock",
    ".md",
    ".py",
    ".sh",
    ".sql",
    ".tf",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_NAMES = {
    ".dockerignore",
    ".editorconfig",
    ".gitignore",
    "Dockerfile",
    "Makefile",
    "NOTICE",
    "SECURITY.md",
}
LEGAL_LINE_LENGTH_EXEMPT = {"LICENSE"}
RMEIO_SECTIONS = (
    "Requires:",
    "Modifies:",
    "Effects:",
    "Inputs:",
    "Outputs:",
)
SNAKE_CASE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
CLASS_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
HEX_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{40,}$")
OPAQUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./:+@#%?&=~-]{81,}$")
JSON_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')


@dataclass(frozen=True)
class Violation:
    """One deterministic project-style violation."""

    path: Path
    line: int
    code: str
    message: str


def tracked_files() -> list[Path]:
    """Return tracked repository files.

    Requires:
        - ROOT is inside a Git working tree.
        - Git is installed and executable.

    Modifies:
        - Nothing.

    Effects:
        - Executes a read-only Git subprocess.

    Inputs:
        - None.

    Outputs:
        - Sorted repository-relative tracked file paths.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    names = result.stdout.decode("utf-8").split("\0")
    return sorted(Path(name) for name in names if name)


def is_text_candidate(path: Path) -> bool:
    """Return whether a tracked file is governed as text.

    Requires:
        - path is repository-relative.

    Modifies:
        - Nothing.

    Effects:
        - Nothing.

    Inputs:
        - path: Tracked repository path.

    Outputs:
        - True when text-style rules apply to the file.
    """
    if path.name in TEXT_NAMES or path.name in LEGAL_LINE_LENGTH_EXEMPT:
        return True
    return path.suffix.lower() in TEXT_SUFFIXES


def normalized_token(token: str) -> str:
    """Strip syntax punctuation surrounding one opaque token.

    Requires:
        - token is one whitespace-delimited source token.

    Modifies:
        - Nothing.

    Effects:
        - Nothing.

    Inputs:
        - token: Candidate token from a source line.

    Outputs:
        - Token with common surrounding punctuation removed.
    """
    return token.strip("'\"`()[]{}<>,;\\")


def is_unsplittable_token(token: str) -> bool:
    """Return whether one long token qualifies for the narrow exception.

    Requires:
        - token is a source token after whitespace splitting.

    Modifies:
        - Nothing.

    Effects:
        - Nothing.

    Inputs:
        - token: Candidate source token.

    Outputs:
        - True only for a long URL, digest, or opaque machine token.
    """
    value = normalized_token(token)
    if len(value) <= MAX_LINE_LENGTH:
        return False
    if value.startswith(("https://", "http://")):
        return True
    if HEX_TOKEN_RE.fullmatch(value):
        return True
    return OPAQUE_TOKEN_RE.fullmatch(value) is not None


def has_unsplittable_content(path: Path, line: str) -> bool:
    """Return whether a long line contains intrinsically unsplittable data.

    Requires:
        - path is repository-relative.
        - line does not include a newline character.

    Modifies:
        - Nothing.

    Effects:
        - Nothing.

    Inputs:
        - path: File containing the source line.
        - line: Source line to inspect.

    Outputs:
        - True when the hard line-length exception is justified.
    """
    if any(is_unsplittable_token(token) for token in line.split()):
        return True
    if path.suffix.lower() == ".json":
        return any(
            len(match.group(0)) > MAX_LINE_LENGTH
            for match in JSON_STRING_RE.finditer(line)
        )
    return False


def check_text(path: Path, data: bytes) -> list[Violation]:
    """Check generic text invariants for one tracked file.

    Requires:
        - path is repository-relative.
        - data contains the tracked file bytes.

    Modifies:
        - Nothing.

    Effects:
        - Decodes the file as UTF-8.

    Inputs:
        - path: File being checked.
        - data: Raw tracked file bytes.

    Outputs:
        - Style violations found in the file.
    """
    violations: list[Violation] = []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [Violation(path, 1, "T001", "text file is not UTF-8")]

    if "\r" in text:
        violations.append(
            Violation(path, 1, "T002", "carriage return found; use LF")
        )
    if data and not data.endswith(b"\n"):
        violations.append(
            Violation(path, len(text.splitlines()), "T003", "missing final newline")
        )

    allow_next = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.rstrip(" \t") != line:
            violations.append(
                Violation(path, number, "T004", "trailing whitespace")
            )
        if "\t" in line and path.name != "Makefile":
            violations.append(
                Violation(path, number, "T005", "tab character is not allowed")
            )
        if (
            len(line) > MAX_LINE_LENGTH
            and path.name not in LEGAL_LINE_LENGTH_EXEMPT
            and not allow_next
            and not has_unsplittable_content(path, line)
        ):
            violations.append(
                Violation(
                    path,
                    number,
                    "L001",
                    f"line is {len(line)} characters; maximum is 80",
                )
            )
        allow_next = line.strip() == ALLOW_NEXT_LINE

    return violations


def is_dunder(name: str) -> bool:
    """Return whether a Python name is a language-protocol dunder name.

    Requires:
        - name is a Python identifier.

    Modifies:
        - Nothing.

    Effects:
        - Nothing.

    Inputs:
        - name: Identifier to inspect.

    Outputs:
        - True when the name begins and ends with two underscores.
    """
    return name.startswith("__") and name.endswith("__")


def project_function_nodes(
    tree: ast.Module,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return module-level functions and direct class methods.

    Requires:
        - tree is a parsed Python module.

    Modifies:
        - Nothing.

    Effects:
        - Nothing.

    Inputs:
        - tree: Parsed Python syntax tree.

    Outputs:
        - Functions that represent repository-level callable contracts.
    """
    functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node)
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.append(item)
    return functions


def requires_rmeio(path: Path) -> bool:
    """Return whether project functions in a Python file require RMEIO.

    Requires:
        - path is repository-relative.

    Modifies:
        - Nothing.

    Effects:
        - Nothing.

    Inputs:
        - path: Python source path.

    Outputs:
        - True for application and Python utility implementation files.
    """
    if not path.parts:
        return False
    if path.parts[0] == "app":
        return True
    return path.parts[0] == "scripts" and path.suffix == ".py"


def check_rmeio(
    path: Path,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[Violation]:
    """Validate one project function's RMEIO contract.

    Requires:
        - node belongs to a file where RMEIO is required.

    Modifies:
        - Nothing.

    Effects:
        - Nothing.

    Inputs:
        - path: Python source path.
        - node: Function or method syntax node.

    Outputs:
        - RMEIO violations for the function.
    """
    if is_dunder(node.name):
        return []
    docstring = ast.get_docstring(node, clean=False)
    if docstring is None:
        return [
            Violation(
                path,
                node.lineno,
                "D001",
                f"function {node.name} requires an RMEIO docstring",
            )
        ]
    positions = [docstring.find(section) for section in RMEIO_SECTIONS]
    if any(position < 0 for position in positions):
        return [
            Violation(
                path,
                node.lineno,
                "D002",
                f"function {node.name} is missing an RMEIO section",
            )
        ]
    if positions != sorted(positions):
        return [
            Violation(
                path,
                node.lineno,
                "D003",
                f"function {node.name} has RMEIO sections out of order",
            )
        ]
    return []


def check_python(path: Path, text: str) -> list[Violation]:
    """Check Python-specific project style rules.

    Requires:
        - path names a UTF-8 Python file.
        - text is the decoded file content.

    Modifies:
        - Nothing.

    Effects:
        - Parses Python syntax into an AST.

    Inputs:
        - path: Python source path.
        - text: Python source text.

    Outputs:
        - Python-specific style violations.
    """
    violations: list[Violation] = []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return [
            Violation(
                path,
                exc.lineno or 1,
                "P001",
                f"Python syntax error: {exc.msg}",
            )
        ]

    module_name = path.stem
    if module_name != "__init__" and not SNAKE_CASE_RE.fullmatch(module_name):
        violations.append(
            Violation(path, 1, "N001", "Python module name must be snake_case")
        )

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not is_dunder(node.name) and not SNAKE_CASE_RE.fullmatch(node.name):
                violations.append(
                    Violation(
                        path,
                        node.lineno,
                        "N002",
                        f"function name is not snake_case: {node.name}",
                    )
                )
        elif isinstance(node, ast.ClassDef):
            if not CLASS_NAME_RE.fullmatch(node.name):
                violations.append(
                    Violation(
                        path,
                        node.lineno,
                        "N003",
                        f"class name is not UpperCamelCase: {node.name}",
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                violations.append(
                    Violation(path, node.lineno, "P002", "wildcard import")
                )
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            violations.append(
                Violation(path, node.lineno, "P003", "bare except clause")
            )

    if requires_rmeio(path):
        for function in project_function_nodes(tree):
            violations.extend(check_rmeio(path, function))

    return violations


def check_shell(path: Path, text: str) -> list[Violation]:
    """Check Bash entrypoint and strict-mode conventions.

    Requires:
        - path names a repository shell program.
        - text is decoded shell source.

    Modifies:
        - Nothing.

    Effects:
        - Nothing.

    Inputs:
        - path: Shell source path.
        - text: Shell source text.

    Outputs:
        - Shell-specific style violations.
    """
    violations: list[Violation] = []
    lines = text.splitlines()
    if not lines or lines[0] != "#!/usr/bin/env bash":
        violations.append(
            Violation(path, 1, "S001", "shell program must use env bash shebang")
        )
    early_lines = lines[1:12]
    if not any(line.strip() == "set -euo pipefail" for line in early_lines):
        violations.append(
            Violation(path, 2, "S002", "shell program must enable strict mode")
        )
    return violations


def check_file(path: Path) -> list[Violation]:
    """Check all automated style rules for one tracked file.

    Requires:
        - path is repository-relative and tracked by Git.

    Modifies:
        - Nothing.

    Effects:
        - Reads the file from the working tree.

    Inputs:
        - path: Tracked repository path.

    Outputs:
        - All automated style violations for the file.
    """
    absolute = ROOT / path
    if not absolute.is_file() or not is_text_candidate(path):
        return []
    data = absolute.read_bytes()
    violations = check_text(path, data)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return violations
    if path.suffix == ".py":
        violations.extend(check_python(path, text))
    if path.suffix == ".sh" or path.as_posix() == "docker/entrypoint.sh":
        violations.extend(check_shell(path, text))
    return violations


def main() -> int:
    """Run the repository style gate.

    Requires:
        - The command runs from a checkout containing Git metadata.

    Modifies:
        - Nothing.

    Effects:
        - Reads tracked files and prints deterministic diagnostics.

    Inputs:
        - None.

    Outputs:
        - Zero when all style checks pass; one when violations exist.
    """
    violations: list[Violation] = []
    for path in tracked_files():
        violations.extend(check_file(path))

    violations.sort(
        key=lambda item: (
            item.path.as_posix(),
            item.line,
            item.code,
            item.message,
        )
    )
    for item in violations:
        print(
            f"{item.path.as_posix()}:{item.line}: "
            f"{item.code} {item.message}"
        )

    if violations:
        print(f"style_check=FAIL violations={len(violations)}")
        return 1
    print("style_check=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
