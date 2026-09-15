from __future__ import annotations

import ast
import re
import subprocess
import textwrap
import tokenize
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_LINE_LENGTH = 80
PYTHON_ROOTS = {"app", "scripts", "tests"}
SKIP_PATHS = {
    Path("scripts/style_check.py"),
    Path(".github/style_migrate.py"),
}


def _run(
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=ROOT,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _tracked_files() -> list[Path]:
    output = _run("git", "ls-files").stdout
    return [Path(value) for value in output.splitlines() if value]


def _wrap_words(
    text: str,
    *,
    prefix: str,
    width: int = MAX_LINE_LENGTH,
) -> list[str]:
    available = max(20, width - len(prefix))
    wrapped = textwrap.wrap(
        text.strip(),
        width=available,
        break_long_words=False,
        break_on_hyphens=False,
    )
    if not wrapped:
        return [prefix.rstrip()]
    return [prefix + part for part in wrapped]


def _wrap_hash_comment(line: str) -> list[str] | None:
    match = re.match(r"^(\s*)#\s?(.*)$", line)
    if match is None or len(line) <= MAX_LINE_LENGTH:
        return None
    indent, content = match.groups()
    return _wrap_words(content, prefix=f"{indent}# ")


def _protected_columns(source: str, line_number: int) -> set[int]:
    protected: set[int] = set()
    try:
        tokens = tokenize.generate_tokens(StringIO(source).readline)
        for token in tokens:
            if token.type not in {tokenize.STRING, tokenize.COMMENT}:
                continue
            start_line, start_col = token.start
            end_line, end_col = token.end
            if not start_line <= line_number <= end_line:
                continue
            if start_line == end_line:
                protected.update(range(start_col, end_col))
            elif line_number == start_line:
                protected.update(range(start_col, 10000))
            elif line_number == end_line:
                protected.update(range(0, end_col))
            else:
                protected.update(range(0, 10000))
    except (tokenize.TokenError, IndentationError):
        return set(range(0, 10000))
    return protected


def _python_parse(lines: list[str]) -> bool:
    try:
        ast.parse("\n".join(lines) + "\n")
    except SyntaxError:
        return False
    return True


def _split_python_code_line(
    lines: list[str],
    index: int,
) -> list[str] | None:
    line = lines[index]
    if len(line) <= MAX_LINE_LENGTH:
        return None
    source = "\n".join(lines) + "\n"
    protected = _protected_columns(source, index + 1)
    indent = re.match(r"^\s*", line).group(0)
    candidates: list[int] = []
    for position, char in enumerate(line):
        if position in protected:
            continue
        if char.isspace() and len(indent) + 8 < position < 76:
            candidates.append(position)
    for position in reversed(candidates):
        left = line[:position].rstrip()
        right = line[position:].lstrip()
        if not left or not right or left.endswith("\\"):
            continue
        replacement = [
            f"{left} \\",
            f"{indent}    {right}",
        ]
        candidate = lines[:index] + replacement + lines[index + 1 :]
        if max(len(value) for value in replacement) >= len(line):
            continue
        if _python_parse(candidate):
            return candidate
    return None


def _docstring_ranges(tree: ast.AST) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr):
            continue
        value = first.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        end_line = getattr(first, "end_lineno", first.lineno)
        ranges.append((first.lineno, end_line))
    return ranges


def _inside_range(
    line_number: int,
    ranges: list[tuple[int, int]],
) -> bool:
    return any(start <= line_number <= end for start, end in ranges)


def _wrap_python_docstrings(lines: list[str]) -> list[str]:
    try:
        tree = ast.parse("\n".join(lines) + "\n")
    except SyntaxError:
        return lines
    ranges = _docstring_ranges(tree)
    output: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        if len(line) <= MAX_LINE_LENGTH:
            output.append(line)
            continue
        if not _inside_range(line_number, ranges):
            output.append(line)
            continue
        stripped = line.strip()
        if not stripped or '"""' in stripped or "'''" in stripped:
            output.append(line)
            continue
        indent = re.match(r"^\s*", line).group(0)
        bullet = re.match(r"^(\s*-\s+)(.*)$", line)
        if bullet is not None:
            prefix, content = bullet.groups()
            wrapped = _wrap_words(content, prefix=prefix)
            output.extend(wrapped)
            continue
        output.extend(_wrap_words(stripped, prefix=indent))
    if _python_parse(output):
        return output
    return lines


def _wrap_python_comments(lines: list[str]) -> list[str]:
    output: list[str] = []
    for line in lines:
        wrapped = _wrap_hash_comment(line)
        if wrapped is None:
            output.append(line)
        else:
            output.extend(wrapped)
    if _python_parse(output):
        return output
    return lines


def _format_python(path: Path) -> None:
    full_path = ROOT / path
    original = full_path.read_text(encoding="utf-8")
    lines = original.splitlines()
    if not _python_parse(lines):
        return
    lines = _wrap_python_docstrings(lines)
    lines = _wrap_python_comments(lines)
    passes = 0
    index = 0
    while index < len(lines) and passes < 10000:
        passes += 1
        if len(lines[index]) <= MAX_LINE_LENGTH:
            index += 1
            continue
        candidate = _split_python_code_line(lines, index)
        if candidate is None:
            index += 1
            continue
        lines = candidate
    if not _python_parse(lines):
        return
    updated = "\n".join(lines) + "\n"
    if updated != original:
        full_path.write_text(updated, encoding="utf-8")


def _outside_shell_quotes(line: str) -> list[int]:
    positions: list[int] = []
    single = False
    double = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and not single:
            escaped = True
            continue
        if char == "'" and not double:
            single = not single
            continue
        if char == '"' and not single:
            double = not double
            continue
        if char.isspace() and not single and not double:
            positions.append(index)
    return positions


def _split_shell_line(line: str) -> list[str] | None:
    if len(line) <= MAX_LINE_LENGTH:
        return None
    indent = re.match(r"^\s*", line).group(0)
    positions = [
        value
        for value in _outside_shell_quotes(line)
        if len(indent) + 8 < value < 76
    ]
    for position in reversed(positions):
        left = line[:position].rstrip()
        right = line[position:].lstrip()
        if not left or not right or left.endswith("\\"):
            continue
        return [f"{left} \\", f"{indent}  {right}"]
    return None


def _format_shell(path: Path) -> None:
    full_path = ROOT / path
    original = full_path.read_text(encoding="utf-8")
    lines = original.splitlines()
    output: list[str] = []
    for line in lines:
        comment = _wrap_hash_comment(line)
        if comment is not None:
            output.extend(comment)
            continue
        pending = [line]
        attempts = 0
        while pending and attempts < 20:
            attempts += 1
            current = pending.pop(0)
            replacement = _split_shell_line(current)
            if replacement is None:
                output.append(current)
            else:
                output.append(replacement[0])
                pending.insert(0, replacement[1])
    candidate = "\n".join(output) + "\n"
    full_path.write_text(candidate, encoding="utf-8")
    result = _run("bash", "-n", str(path), check=False)
    if result.returncode != 0:
        full_path.write_text(original, encoding="utf-8")


def _format_env(path: Path) -> None:
    full_path = ROOT / path
    original = full_path.read_text(encoding="utf-8")
    output: list[str] = []
    for line in original.splitlines():
        comment = _wrap_hash_comment(line)
        if comment is None:
            output.append(line)
        else:
            output.extend(comment)
    candidate = "\n".join(output) + "\n"
    if candidate != original:
        full_path.write_text(candidate, encoding="utf-8")


def _format_markdown(path: Path) -> None:
    full_path = ROOT / path
    original = full_path.read_text(encoding="utf-8")
    lines = original.splitlines()
    output: list[str] = []
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            output.append(line)
            continue
        if in_fence or len(line) <= MAX_LINE_LENGTH:
            output.append(line)
            continue
        if stripped.startswith("|") or stripped.startswith("<"):
            output.append(line)
            continue
        match = re.match(
            r"^(\s*(?:[-*+] |\d+\. |>|#+ ))(.*)$",
            line,
        )
        if match is not None:
            prefix, content = match.groups()
            output.extend(_wrap_words(content, prefix=prefix))
            continue
        indent = re.match(r"^\s*", line).group(0)
        output.extend(_wrap_words(stripped, prefix=indent))
    candidate = "\n".join(output) + "\n"
    if candidate != original:
        full_path.write_text(candidate, encoding="utf-8")


def _split_sql_line(line: str) -> list[str] | None:
    positions = _outside_shell_quotes(line)
    positions = [value for value in positions if 20 < value < 76]
    if not positions:
        return None
    position = positions[-1]
    indent = re.match(r"^\s*", line).group(0)
    return [line[:position].rstrip(), indent + line[position:].lstrip()]


def _format_sql(path: Path) -> None:
    full_path = ROOT / path
    original = full_path.read_text(encoding="utf-8")
    output: list[str] = []
    for line in original.splitlines():
        pending = [line]
        while pending:
            current = pending.pop(0)
            if len(current) <= MAX_LINE_LENGTH:
                output.append(current)
                continue
            replacement = _split_sql_line(current)
            if replacement is None:
                output.append(current)
                continue
            output.append(replacement[0])
            pending.insert(0, replacement[1])
    candidate = "\n".join(output) + "\n"
    if candidate != original:
        full_path.write_text(candidate, encoding="utf-8")


def _yaml_block_shell(lines: list[str]) -> set[int]:
    shell_lines: set[int] = set()
    active_indent: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if active_indent is not None:
            if stripped and indent <= active_indent:
                active_indent = None
            else:
                shell_lines.add(index)
                continue
        if re.search(r"\brun:\s*[|>]", stripped):
            active_indent = indent
    return shell_lines


def _fold_yaml_scalar(line: str) -> list[str] | None:
    match = re.match(r"^(\s*)([A-Za-z0-9_.-]+):\s+(.+)$", line)
    if match is None:
        return None
    indent, key, value = match.groups()
    if value.startswith(("|", ">")):
        return None
    if value.startswith("${{") and value.endswith("}}"):
        pass
    elif value[0] in "[{" or value.startswith("&"):
        return None
    prefix = f"{indent}{key}: >-"
    continuation = indent + "  "
    wrapped = _wrap_words(value, prefix=continuation)
    if any(len(item) > MAX_LINE_LENGTH for item in wrapped):
        return None
    return [prefix, *wrapped]


def _format_yaml(path: Path) -> None:
    full_path = ROOT / path
    original = full_path.read_text(encoding="utf-8")
    lines = original.splitlines()
    shell_lines = _yaml_block_shell(lines)
    output: list[str] = []
    for index, line in enumerate(lines):
        comment = _wrap_hash_comment(line)
        if comment is not None:
            output.extend(comment)
            continue
        if len(line) <= MAX_LINE_LENGTH:
            output.append(line)
            continue
        if index in shell_lines:
            replacement = _split_shell_line(line)
            if replacement is None:
                output.append(line)
            else:
                output.extend(replacement)
            continue
        folded = _fold_yaml_scalar(line)
        if folded is None:
            output.append(line)
        else:
            output.extend(folded)
    candidate = "\n".join(output) + "\n"
    if candidate != original:
        full_path.write_text(candidate, encoding="utf-8")


def _format_dockerfile(path: Path) -> None:
    full_path = ROOT / path
    original = full_path.read_text(encoding="utf-8")
    output: list[str] = []
    for line in original.splitlines():
        comment = _wrap_hash_comment(line)
        if comment is not None:
            output.extend(comment)
            continue
        pending = [line]
        while pending:
            current = pending.pop(0)
            replacement = _split_shell_line(current)
            if replacement is None:
                output.append(current)
                continue
            output.append(replacement[0])
            pending.insert(0, "    " + replacement[1].lstrip())
    candidate = "\n".join(output) + "\n"
    if candidate != original:
        full_path.write_text(candidate, encoding="utf-8")


def _terraform_string_assignment(line: str) -> list[str] | None:
    match = re.match(
        r'^(\s*)([A-Za-z0-9_]+)\s*=\s*"([^"\\]+)"\s*$',
        line,
    )
    if match is None:
        return None
    indent, name, value = match.groups()
    words = value.split(" ")
    if len(words) < 2:
        return None
    chunks: list[str] = []
    current = ""
    width = MAX_LINE_LENGTH - len(indent) - 6
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if current and len(candidate) > width:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    if len(chunks) < 2:
        return None
    output = [f'{indent}{name} = join(" ", [']
    for chunk in chunks:
        output.append(f'{indent}  "{chunk}",')
    output.append(f"{indent}])")
    return output


def _terraform_expression(line: str) -> list[str] | None:
    match = re.match(r"^(\s*)([A-Za-z0-9_]+)\s*=\s*(.+)$", line)
    if match is None:
        return None
    indent, name, value = match.groups()
    if len(value) <= MAX_LINE_LENGTH - len(indent) - 2:
        return [f"{indent}{name} = (", f"{indent}  {value}", f"{indent})"]
    pieces = re.split(r"(\s+(?:&&|\|\|)\s+|,\s+)", value)
    if len(pieces) <= 1:
        return None
    output = [f"{indent}{name} = ("]
    current = indent + "  "
    for piece in pieces:
        candidate = current + piece
        if len(candidate) > MAX_LINE_LENGTH and current.strip():
            output.append(current.rstrip())
            current = indent + "  " + piece.lstrip()
        else:
            current = candidate
    if current.strip():
        output.append(current.rstrip())
    output.append(f"{indent})")
    return output


def _format_terraform(path: Path) -> None:
    full_path = ROOT / path
    original = full_path.read_text(encoding="utf-8")
    output: list[str] = []
    for line in original.splitlines():
        comment = _wrap_hash_comment(line)
        if comment is not None:
            output.extend(comment)
            continue
        if len(line) <= MAX_LINE_LENGTH:
            output.append(line)
            continue
        replacement = _terraform_string_assignment(line)
        if replacement is None:
            replacement = _terraform_expression(line)
        if replacement is None:
            output.append(line)
        else:
            output.extend(replacement)
    candidate = "\n".join(output) + "\n"
    if candidate != original:
        full_path.write_text(candidate, encoding="utf-8")


def _format_generic_comments(path: Path) -> None:
    full_path = ROOT / path
    original = full_path.read_text(encoding="utf-8")
    output: list[str] = []
    for line in original.splitlines():
        comment = _wrap_hash_comment(line)
        if comment is None:
            output.append(line)
        else:
            output.extend(comment)
    candidate = "\n".join(output) + "\n"
    if candidate != original:
        full_path.write_text(candidate, encoding="utf-8")


def _migrate() -> None:
    for path in _tracked_files():
        if path.parts[:2] == (".github", "workflows"):
            continue
        if path in SKIP_PATHS:
            continue
        if path.suffix == ".py" and path.parts[0] in PYTHON_ROOTS:
            _format_python(path)
        elif path.suffix == ".sh":
            _format_shell(path)
        elif path.suffix == ".md" and path.name != "LICENSE":
            _format_markdown(path)
        elif path.name.endswith(".env.example"):
            _format_env(path)
        elif path.suffix in {".yaml", ".yml"}:
            _format_yaml(path)
        elif path.suffix == ".sql":
            _format_sql(path)
        elif path.suffix == ".tf":
            _format_terraform(path)
        elif path.name == "Dockerfile":
            _format_dockerfile(path)
        elif path.suffix in {".toml", ".tfvars"}:
            _format_generic_comments(path)


if __name__ == "__main__":
    _migrate()
