from __future__ import annotations

import argparse
import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_FILE = ROOT / ".style-baseline"
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
    ".style-baseline",
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
_LONG_ATOM_RE = re.compile(r"\S{81,}")
_RMEIO_HEADINGS = (
    "Requires:",
    "Modifies:",
    "Effects:",
    "Inputs:",
    "Outputs:",
)


@dataclass(frozen=True)
class Violation:
    """Represent one project style violation.

    Requires:
        path identifies a repository-relative file.
    Modifies:
        Nothing.
    Effects:
        Stores one immutable diagnostic record.
    Inputs:
        path: Repository-relative file path.
        line: One-based line number, or zero for file-level errors.
        rule: Stable rule identifier.
        message: Human-readable diagnostic text.
    Outputs:
        An immutable violation value.
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

    Requires:
        Git is installed and ROOT is inside a Git working tree.
    Modifies:
        Nothing.
    Effects:
        Executes a Git subprocess and captures its output.
    Inputs:
        args: Git arguments after the executable name.
        check: Whether non-zero exit status raises an exception.
    Outputs:
        Completed subprocess result with text output.
    """
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _baseline_commit() -> str:
    """Return the commit that predates project style enforcement.

    Requires:
        BASELINE_FILE contains one full Git commit SHA.
    Modifies:
        Nothing.
    Effects:
        Reads the repository-owned style baseline file.
    Inputs:
        None.
    Outputs:
        Forty-character lowercase Git commit SHA.
    """
    baseline = BASELINE_FILE.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", baseline):
        raise ValueError("invalid .style-baseline commit")
    return baseline


def _baseline_available(baseline: str) -> bool:
    """Return whether the baseline commit exists in the local clone.

    Requires:
        baseline is a full Git commit SHA.
    Modifies:
        Nothing.
    Effects:
        Queries the local Git object database.
    Inputs:
        baseline: Commit used for transitional style comparison.
    Outputs:
        True when Git can resolve the baseline commit locally.
    """
    result = _run_git(
        "cat-file",
        "-e",
        f"{baseline}^{{commit}}",
        check=False,
    )
    return result.returncode == 0


def _tracked_files() -> list[Path]:
    """Return tracked text candidates in deterministic order.

    Requires:
        The repository index is readable by Git.
    Modifies:
        Nothing.
    Effects:
        Reads tracked paths from Git.
    Inputs:
        None.
    Outputs:
        Sorted repository-relative paths eligible for style checks.
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


def _changed_since_baseline(path: Path, baseline: str) -> bool:
    """Return whether a tracked file differs from the style baseline.

    Requires:
        baseline exists in the local Git object database.
        path is repository-relative and currently tracked.
    Modifies:
        Nothing.
    Effects:
        Compares current tracked content with the baseline commit.
    Inputs:
        path: Repository-relative path.
        baseline: Commit predating style enforcement.
    Outputs:
        True for new or modified files, otherwise False.
    """
    result = _run_git(
        "diff",
        "--quiet",
        baseline,
        "--",
        str(path),
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"unable to compare {path} with style baseline"
        )
    return result.returncode == 1


def _line_length_exception(path: Path, line: str) -> bool:
    """Return whether an overlong line is indivisible by project policy.

    Requires:
        line does not include its trailing newline.
    Modifies:
        Nothing.
    Effects:
        None.
    Inputs:
        path: Repository-relative path containing the line.
        line: Candidate overlong line.
    Outputs:
        True only for narrow atomic forms that should not be split.
    """
    stripped = line.strip()
    if not stripped:
        return False
    if path.suffix == ".md" and stripped.startswith("|"):
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

    Requires:
        path exists under ROOT and contains tracked text.
    Modifies:
        Nothing.
    Effects:
        Reads the file contents.
    Inputs:
        path: Repository-relative file path.
    Outputs:
        Violations for universal text style rules.
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

    Requires:
        path is repository-relative.
    Modifies:
        Nothing.
    Effects:
        None.
    Inputs:
        path: Repository-relative path.
    Outputs:
        True for app modules and Python utilities under scripts.
    """
    return (
        path.suffix == ".py"
        and path.parts[0] in {"app", "scripts"}
        and path.name != "style_check.py"
    )


def _docstring_has_full_rmeio(node: ast.AST) -> bool:
    """Return whether a node docstring contains every RMEIO heading.

    Requires:
        node supports an AST docstring.
    Modifies:
        Nothing.
    Effects:
        None.
    Inputs:
        node: Function or method AST node.
    Outputs:
        True when all required RMEIO headings are present.
    """
    docstring = ast.get_docstring(node, clean=False) or ""
    return all(
        heading in docstring
        for heading in _RMEIO_HEADINGS
    )


def _requires_rmeio(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Return whether a function must use the full RMEIO contract.

    Requires:
        node is a parsed function definition.
    Modifies:
        Nothing.
    Effects:
        None.
    Inputs:
        node: Parsed function definition.
    Outputs:
        True for non-trivial project functions.
    """
    if node.name.startswith("__") and node.name.endswith("__"):
        return False
    if node.name.startswith("_") and len(node.body) <= 3:
        return False
    return True


def _check_python(path: Path) -> list[Violation]:
    """Check Python-specific structural rules without external linters.

    Requires:
        path identifies UTF-8 Python source under ROOT.
    Modifies:
        Nothing.
    Effects:
        Parses source into an AST.
    Inputs:
        path: Repository-relative Python path.
    Outputs:
        Python-specific style violations.
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
            if _is_runtime_python(path) and _requires_rmeio(node):
                if not _docstring_has_full_rmeio(node):
                    violations.append(
                        Violation(
                            str(path),
                            node.lineno,
                            "PY006",
                            f"{node.name} requires full RMEIO docstring",
                        )
                    )
    return violations


def collect_violations(
    *,
    strict: bool,
) -> tuple[list[Violation], int, int]:
    """Collect machine-checkable project style violations.

    Requires:
        Repository files are readable and Git is available.
        The baseline commit is available for non-strict checks.
    Modifies:
        Nothing.
    Effects:
        Reads tracked files and parses selected Python source.
    Inputs:
        strict: Whether to check unchanged pre-standard files too.
    Outputs:
        Violations, checked-file count, and skipped legacy-file count.
    """
    baseline = _baseline_commit()
    if not strict and not _baseline_available(baseline):
        raise RuntimeError(
            "style baseline is unavailable; use a full Git clone"
        )

    violations: list[Violation] = []
    checked_files = 0
    legacy_skipped = 0
    for path in _tracked_files():
        if not strict and not _changed_since_baseline(path, baseline):
            legacy_skipped += 1
            continue
        checked_files += 1
        violations.extend(_check_text_file(path))
        if path.suffix == ".py":
            violations.extend(_check_python(path))

    violations.sort(
        key=lambda item: (
            item.path,
            item.line,
            item.rule,
            item.message,
        )
    )
    return violations, checked_files, legacy_skipped


def _build_parser() -> argparse.ArgumentParser:
    """Build the project style command-line parser.

    Requires:
        Nothing.
    Modifies:
        Nothing.
    Effects:
        None.
    Inputs:
        None.
    Outputs:
        Configured argument parser.
    """
    parser = argparse.ArgumentParser(
        description="Check Secure AI Gateway project style.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="check the entire tree, including pre-standard files",
    )
    return parser


def main() -> int:
    """Run the repository style gate and print stable diagnostics.

    Requires:
        The command runs from a Git checkout of this project.
    Modifies:
        Nothing.
    Effects:
        Prints style violations and migration status.
    Inputs:
        Command-line arguments parsed by `_build_parser`.
    Outputs:
        Process status 0 when clean, otherwise 1.
    """
    args = _build_parser().parse_args()
    try:
        violations, checked_files, legacy_skipped = collect_violations(
            strict=args.strict,
        )
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

    mode = "strict" if args.strict else "incremental"
    if violations:
        print(
            f"style_check=FAIL mode={mode} "
            f"violations={len(violations)} "
            f"checked_files={checked_files} "
            f"legacy_skipped={legacy_skipped}"
        )
        return 1

    print(
        f"style_check=PASS mode={mode} violations=0 "
        f"checked_files={checked_files} "
        f"legacy_skipped={legacy_skipped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
