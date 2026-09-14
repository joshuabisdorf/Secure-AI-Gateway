from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "requirements" / "release.lock"
PYPROJECT_PATH = ROOT / "pyproject.toml"
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+")
EXACT_PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s]+)$")
DIRECT_URL_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*@\s*https://[^\s]+#sha256=([0-9a-f]{64})$")


class LockVerificationError(RuntimeError):
    """A release dependency-lock invariant failed."""


def normalize_name(name: str) -> str:
    """Normalize a Python distribution name for requirement comparisons.

    Requires:
        name is a non-empty distribution name.
    Modifies:
        Nothing.
    Effects:
        Performs no I/O.
    Inputs:
        name: Python distribution name.
    Outputs:
        PEP 503-style lowercase name with separator runs converted to hyphens.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def dependency_name(requirement: str) -> str:
    """Extract the normalized distribution name from a project requirement.

    Requires:
        requirement begins with a valid distribution name.
    Modifies:
        Nothing.
    Effects:
        Raises LockVerificationError for an unsupported requirement form.
    Inputs:
        requirement: Requirement string from pyproject.toml.
    Outputs:
        Normalized distribution name without extras or version constraints.
    """
    match = NAME_RE.match(requirement.strip())
    if match is None:
        raise LockVerificationError(f"cannot parse project dependency: {requirement}")
    return normalize_name(match.group(0))


def read_lock_entries() -> dict[str, str]:
    """Read and validate release-lock entry syntax.

    Requires:
        LOCK_PATH exists and is UTF-8 text.
    Modifies:
        Nothing.
    Effects:
        Reads the release lock and raises LockVerificationError on invalid,
        duplicate, unpinned, or unhashed direct-URL entries.
    Inputs:
        None.
    Outputs:
        Mapping of normalized distribution name to its validated lock line.
    """
    entries: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        LOCK_PATH.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-", ".")):
            raise LockVerificationError(
                f"release lock line {line_number} uses an unsupported option/path: {line}"
            )

        exact_match = EXACT_PIN_RE.fullmatch(line)
        direct_match = DIRECT_URL_RE.fullmatch(line)
        match = exact_match or direct_match
        if match is None:
            raise LockVerificationError(
                f"release lock line {line_number} is not exact/hash pinned: {line}"
            )

        name = normalize_name(match.group(1))
        if name in entries:
            raise LockVerificationError(f"duplicate release lock entry: {name}")
        entries[name] = line

    if not entries:
        raise LockVerificationError("release lock is empty")
    return entries


def read_project_dependencies() -> tuple[set[str], list[str]]:
    """Read production dependencies and build requirements from pyproject.toml.

    Requires:
        PYPROJECT_PATH exists and contains project/build-system tables.
    Modifies:
        Nothing.
    Effects:
        Reads and parses pyproject.toml.
    Inputs:
        None.
    Outputs:
        A pair containing normalized production dependency names and raw build
        requirement strings.
    """
    document = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    project_dependencies = {
        dependency_name(requirement)
        for requirement in document["project"].get("dependencies", [])
    }
    build_requirements = list(document["build-system"].get("requires", []))
    return project_dependencies, build_requirements


def verify_release_lock() -> None:
    """Verify the committed release lock covers the declared runtime surface.

    Requires:
        The release lock and pyproject.toml are present and readable.
    Modifies:
        Nothing.
    Effects:
        Reads project metadata and raises LockVerificationError if runtime roots
        are missing or the build backend is not exactly pinned.
    Inputs:
        None.
    Outputs:
        None on success.
    """
    lock_entries = read_lock_entries()
    project_dependencies, build_requirements = read_project_dependencies()

    missing = sorted(project_dependencies - set(lock_entries))
    if missing:
        raise LockVerificationError(
            "release lock is missing project dependencies: " + ", ".join(missing)
        )

    if build_requirements != ["setuptools==84.0.0"]:
        raise LockVerificationError(
            "build-system requires must be exactly pinned to setuptools==84.0.0"
        )

    model_line = lock_entries.get("en-core-web-sm", "")
    if "#sha256=" not in model_line:
        raise LockVerificationError("spaCy model direct artifact must be SHA-256 pinned")


def main() -> int:
    """Run release-lock verification and print a compact success signal.

    Requires:
        Repository metadata and release lock are available under ROOT.
    Modifies:
        Nothing.
    Effects:
        Reads release metadata and writes the verification result to stdout.
    Inputs:
        None.
    Outputs:
        Process exit code 0 on success; exceptions make verification fail closed.
    """
    verify_release_lock()
    entries = read_lock_entries()
    print(f"release_lock=OK entries={len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
