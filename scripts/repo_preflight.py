from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHA_PIN_RE = re.compile(r"^[0-9a-fA-F]{40}$")
USES_RE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)
SECRET_MARKERS = (
    re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bghp_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
REQUIRED_PATHS = (
    "README.md",
    "LICENSE",
    "NOTICE",
    "SECURITY.md",
    "CHANGELOG.md",
    "docs/demo.md",
    "docs/release-checklist.md",
    "docs/security-review.md",
    ".github/workflows/ci.yml",
    ".github/workflows/codeql.yml",
    ".github/workflows/container-security.yml",
    ".github/workflows/release.yml",
)


class PreflightFailure(RuntimeError):
    """A release-preflight invariant failed."""


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run Git from the repository root.

    Args:
        args: Git command arguments after the `git` executable. check: Whether
            a non-zero exit status should raise an exception.

    Returns:
        The completed subprocess result with text stdout/stderr.

    Notes:
        - Precondition: ROOT is inside a Git working tree and Git is installed.
        - Side effect: No repository files or Git refs.
    """
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def tracked_files() -> list[str]:
    """Return all files tracked in the current Git index.

    Returns:
        Sorted repository-relative tracked file paths.

    Notes:
        - Precondition: The repository is readable by Git.
    """
    output = run_git("ls-files").stdout
    return sorted(line for line in output.splitlines() if line)


def check_required_paths(files: list[str]) -> None:
    """Verify release-critical files are tracked.

    Args:
        files: Current tracked file paths.

    Returns:
        None on success.

    Notes:
        - Precondition: files contains repository-relative tracked paths.
    """
    tracked = set(files)
    missing = [path for path in REQUIRED_PATHS if path not in tracked]
    if missing:
        raise PreflightFailure("missing required paths: " + ", ".join(missing))


def check_tracked_artifacts(files: list[str]) -> None:
    """Reject tracked local secrets, state files, and generated Python \
        artifacts.

    Args:
        files: Current tracked file paths.

    Returns:
        None on success.

    Notes:
        - Precondition: files contains repository-relative tracked paths.
    """
    forbidden: list[str] = []
    for path in files:
        p = Path(path)
        name = p.name
        lower = name.lower()
        if "__pycache__" in p.parts or lower.endswith((".pyc", ".pyo")):
            forbidden.append(path)
            continue
        if lower in {".env", ".pypirc", "id_rsa", "id_ed25519"}:
            forbidden.append(path)
            continue
        if lower.endswith((".tfstate", ".tfstate.backup", ".pem", ".key")):
            forbidden.append(path)
    if forbidden:
        raise PreflightFailure(
            "forbidden tracked artifacts: " + ", ".join(forbidden)
        )


def check_action_pins(files: list[str]) -> None:
    """Require third-party GitHub Actions to use immutable 40-character SHA \
        pins.

    Args:
        files: Current tracked file paths.

    Returns:
        None on success.

    Notes:
        - Precondition: Workflow files are UTF-8 YAML text.
    """
    failures: list[str] = []
    workflow_paths = [
        path
        for path in files
        if path.startswith(".github/workflows/")
        and path.endswith((".yml", ".yaml"))
    ]
    for path in workflow_paths:
        text = (ROOT / path).read_text(encoding="utf-8")
        for use in USES_RE.findall(text):
            if use.startswith(("./", "docker://")):
                continue
            if "@" not in use:
                failures.append(f"{path}: {use} has no ref")
                continue
            _, ref = use.rsplit("@", 1)
            if not SHA_PIN_RE.fullmatch(ref):
                failures.append(f"{path}: {use} is not SHA-pinned")
    if failures:
        raise PreflightFailure(
            "mutable GitHub Action refs:\n  " + "\n  ".join(failures)
        )


def check_current_tree_secret_markers(files: list[str]) -> None:
    """Scan tracked UTF-8 text for high-confidence credential markers.

    Args:
        files: Current tracked file paths.

    Returns:
        None on success.

    Notes:
        - Precondition: Tracked files are readable from the working tree.
    """
    findings: list[str] = []
    for path in files:
        candidate = ROOT / path
        try:
            if candidate.stat().st_size > 1_000_000:
                continue
            text = candidate.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for marker in SECRET_MARKERS:
            if marker.search(text):
                findings.append(path)
                break
    if findings:
        raise PreflightFailure(
            "high-confidence credential markers found in tracked files: "
            + ", ".join(sorted(findings))
        )


def check_history_sensitive_filenames() -> None:
    """Inspect full Git history for filenames that commonly contain local \
        secrets.

    Returns:
        None on success.

    Notes:
        - Precondition: A non-shallow clone containing the repository history.
    """
    shallow = run_git("rev-parse", "--is-shallow-repository").stdout.strip()
    if shallow == "true":
        raise PreflightFailure(
            "history scan requires a full clone; fetch full history before"
            " using --history"
        )
    names = run_git(
        "log", "--all", "--name-only", "--format="
    ).stdout.splitlines()
    suspicious: set[str] = set()
    for path in names:
        if not path:
            continue
        p = Path(path)
        lower = p.name.lower()
        if lower in {".env", ".pypirc", "id_rsa", "id_ed25519"}:
            suspicious.add(path)
        elif lower.endswith((".tfstate", ".tfstate.backup", ".pem", ".key")):
            suspicious.add(path)
    if suspicious:
        raise PreflightFailure(
            "sensitive-looking filenames exist in Git history: "
            + ", ".join(sorted(suspicious))
        )


def check_clean_tree(allow_dirty: bool) -> None:
    """Require a clean worktree for a release-oriented local preflight.

    Args:
        allow_dirty: Whether uncommitted changes are permitted.

    Returns:
        None on success.

    Notes:
        - Precondition: Git can inspect the working tree.
    """
    if allow_dirty:
        return
    status = run_git("status", "--porcelain").stdout.strip()
    if status:
        raise PreflightFailure("working tree is not clean")


def parse_args() -> argparse.Namespace:
    """Parse command-line options.

    Returns:
        Parsed argparse namespace.

    Notes:
        - Precondition: Command-line arguments are available through Python's
          process state.
    """
    parser = argparse.ArgumentParser(
        description="Secure AI Gateway repository preflight"
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow uncommitted working-tree changes",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="also inspect full Git history for sensitive-looking filenames",
    )
    return parser.parse_args()


def main() -> int:
    """Run repository hygiene and release-readiness checks.

    Returns:
        Process exit code 0 on success and 1 on a failed invariant.

    Notes:
        - Input: Command-line options from parse_args().
        - Precondition: The script is executed from a usable Git checkout.
        - Side effect: No files, refs, credentials, or external services.
    """
    args = parse_args()
    checks = [
        ("required_paths", lambda files: check_required_paths(files)),
        ("tracked_artifacts", lambda files: check_tracked_artifacts(files)),
        ("action_sha_pins", lambda files: check_action_pins(files)),
        (
            "secret_markers",
            lambda files: check_current_tree_secret_markers(files),
        ),
    ]
    try:
        files = tracked_files()
        for label, check in checks:
            check(files)
            print(f"PASS check={label}")
        check_clean_tree(args.allow_dirty)
        print(
            "PASS check=clean_tree"
            if not args.allow_dirty
            else "PASS check=clean_tree skipped=true"
        )
        if args.history:
            check_history_sensitive_filenames()
            print("PASS check=history_sensitive_filenames")
    except (PreflightFailure, subprocess.CalledProcessError) as exc:
        print(f"FAIL preflight={exc}")
        return 1

    print("secure_ai_gateway_repo_preflight=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
