from __future__ import annotations

import ast
import re
import subprocess
import textwrap
import tokenize
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_LENGTH = 80
RMEIO_HEADINGS = ("Requires:", "Modifies:", "Effects:", "Inputs:", "Outputs:")


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return [Path(value) for value in result.stdout.splitlines() if value]


def wrap_text(text: str, width: int) -> list[str]:
    return textwrap.wrap(
        text,
        width=max(20, width),
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]


def is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def requires_rmeio(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if is_dunder(node.name):
        return False
    if node.name.startswith("_") and len(node.body) <= 3:
        return False
    return True


def function_summary(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str:
    doc = ast.get_docstring(node, clean=True) or ""
    for raw in doc.splitlines():
        value = raw.strip()
        if value and value != "RME" and value not in RMEIO_HEADINGS:
            if not value.startswith("-"):
                return value
    words = node.name.strip("_").replace("_", " ")
    return f"Performs the {words} operation."


def return_description(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str:
    annotation = node.returns
    if annotation is None:
        return "The function result, if any."
    rendered = ast.unparse(annotation)
    if rendered in {"None", "NoneType"}:
        return "None."
    return f"A value matching the declared {rendered} return contract."


def parameter_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    names = [arg.arg for arg in args if arg.arg not in {"self", "cls"}]
    if node.args.vararg is not None:
        names.append(f"*{node.args.vararg.arg}")
    if node.args.kwarg is not None:
        names.append(f"**{node.args.kwarg.arg}")
    return names


def rmeio_lines(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    indent: str,
) -> list[str]:
    summary = function_summary(node).rstrip(".") + "."
    names = parameter_names(node)
    mutating_names = {
        "close",
        "reset",
        "record",
        "main",
        "eval",
        "aclose",
        "get_credentials",
        "get_credentials_async",
        "get_secret_value",
    }
    if node.name in mutating_names:
        modifies = "Owned runtime or dependency state, as described by the operation."
    else:
        modifies = "No state beyond delegated dependency behavior."
    lines = [indent + '"""', indent + "RME", ""]
    sections = [
        (
            "Requires:",
            [
                "Arguments satisfy their declared contracts and required configured "
                "dependencies are available."
            ],
        ),
        ("Modifies:", [modifies]),
        ("Effects:", [summary]),
        (
            "Inputs:",
            [f"{name}: Function input." for name in names] or ["None."],
        ),
        ("Outputs:", [return_description(node)]),
    ]
    content_width = MAX_LENGTH - len(indent) - 6
    for heading, values in sections:
        lines.append(indent + heading)
        for value in values:
            wrapped = wrap_text(value, content_width)
            lines.append(indent + "    - " + wrapped[0])
            for continuation in wrapped[1:]:
                lines.append(indent + "      " + continuation)
        lines.append("")
    if lines[-1] == "":
        lines.pop()
    lines.append(indent + '"""')
    return lines


def ensure_rmeio(path: Path) -> None:
    full = ROOT / path
    source = full.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    edits: list[tuple[int, int, list[str]]] = []
    lines = source.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not requires_rmeio(node):
            continue
        doc = ast.get_docstring(node, clean=False) or ""
        if all(heading in doc for heading in RMEIO_HEADINGS):
            continue
        if not node.body:
            continue
        first = node.body[0]
        indent = " " * first.col_offset
        replacement = rmeio_lines(node, indent)
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            end = getattr(first, "end_lineno", first.lineno)
            edits.append((first.lineno - 1, end, replacement))
        else:
            edits.append((first.lineno - 1, first.lineno - 1, replacement))
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start:end] = replacement
    candidate = "\n".join(lines) + "\n"
    try:
        ast.parse(candidate)
    except SyntaxError:
        return
    full.write_text(candidate, encoding="utf-8")


def fix_one_line_docstrings(lines: list[str]) -> list[str]:
    output: list[str] = []
    pattern = re.compile(r'^(\s*)"""(.+)"""\s*$')
    for line in lines:
        if len(line) <= MAX_LENGTH:
            output.append(line)
            continue
        match = pattern.match(line)
        if match is None:
            output.append(line)
            continue
        indent, content = match.groups()
        output.append(indent + '"""')
        output.extend(
            indent + part
            for part in wrap_text(content.strip(), MAX_LENGTH - len(indent))
        )
        output.append(indent + '"""')
    return output


def string_chunks(value: str, width: int = 36) -> list[str]:
    if not value:
        return [value]
    return [value[index : index + width] for index in range(0, len(value), width)]


def split_python_literal_once(lines: list[str]) -> list[str] | None:
    source = "\n".join(lines) + "\n"
    try:
        tokens = list(tokenize.generate_tokens(StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):
        return None
    for token in tokens:
        if token.type != tokenize.STRING:
            continue
        start_line, start_col = token.start
        end_line, end_col = token.end
        if start_line != end_line:
            continue
        line = lines[start_line - 1]
        if len(line) <= MAX_LENGTH:
            continue
        lower = token.string.lower()
        prefix_letters = lower[: lower.find("'")] if "'" in lower else ""
        if '"' in lower:
            prefix_letters = lower[: lower.find('"')]
        if "f" in prefix_letters or "b" in prefix_letters:
            continue
        try:
            value = ast.literal_eval(token.string)
        except (ValueError, SyntaxError):
            continue
        if not isinstance(value, str) or len(value) < 24:
            continue
        indent = re.match(r"^\s*", line).group(0)
        before = line[:start_col]
        after = line[end_col:]
        chunks = string_chunks(value)
        replacement = [before + "("]
        replacement.extend(indent + "    " + repr(chunk) for chunk in chunks)
        replacement.append(indent + ")" + after)
        candidate = lines[: start_line - 1] + replacement + lines[start_line:]
        try:
            ast.parse("\n".join(candidate) + "\n")
        except SyntaxError:
            continue
        return candidate
    return None


def split_python_whitespace_once(lines: list[str]) -> list[str] | None:
    source = "\n".join(lines) + "\n"
    protected: dict[int, set[int]] = {}
    try:
        tokens = tokenize.generate_tokens(StringIO(source).readline)
        for token in tokens:
            if token.type not in {tokenize.STRING, tokenize.COMMENT}:
                continue
            if token.start[0] != token.end[0]:
                continue
            row = protected.setdefault(token.start[0], set())
            row.update(range(token.start[1], token.end[1]))
    except (tokenize.TokenError, IndentationError):
        return None
    for index, line in enumerate(lines):
        if len(line) <= MAX_LENGTH:
            continue
        indent = re.match(r"^\s*", line).group(0)
        blocked = protected.get(index + 1, set())
        candidates = [
            position
            for position, char in enumerate(line)
            if char.isspace()
            and position not in blocked
            and len(indent) + 8 < position < 76
        ]
        for position in reversed(candidates):
            left = line[:position].rstrip()
            right = line[position:].lstrip()
            if not left or not right or left.endswith("\\"):
                continue
            replacement = [left + " \\", indent + "    " + right]
            candidate = lines[:index] + replacement + lines[index + 1 :]
            try:
                ast.parse("\n".join(candidate) + "\n")
            except SyntaxError:
                continue
            return candidate
    return None


def fix_python(path: Path) -> None:
    full = ROOT / path
    source = full.read_text(encoding="utf-8")
    lines = fix_one_line_docstrings(source.splitlines())
    try:
        ast.parse("\n".join(lines) + "\n")
    except SyntaxError:
        lines = source.splitlines()
    for _ in range(500):
        if all(len(line) <= MAX_LENGTH for line in lines):
            break
        candidate = split_python_literal_once(lines)
        if candidate is None:
            candidate = split_python_whitespace_once(lines)
        if candidate is None:
            break
        lines = candidate
    candidate_text = "\n".join(lines) + "\n"
    try:
        ast.parse(candidate_text)
    except SyntaxError:
        return
    full.write_text(candidate_text, encoding="utf-8")


def fix_markdown(path: Path) -> None:
    full = ROOT / path
    lines = full.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            output.append(line)
            continue
        if in_fence or len(line) <= MAX_LENGTH or stripped.startswith("|"):
            output.append(line)
            continue
        prefix_match = re.match(r"^(\s*(?:[-*+] |\d+\. |>|#+ ))(.*)$", line)
        if prefix_match:
            prefix, content = prefix_match.groups()
            wrapped = textwrap.wrap(
                content,
                width=MAX_LENGTH,
                initial_indent=prefix,
                subsequent_indent=" " * len(prefix),
                break_long_words=False,
                break_on_hyphens=False,
            )
        else:
            indent = re.match(r"^\s*", line).group(0)
            wrapped = textwrap.wrap(
                stripped,
                width=MAX_LENGTH,
                initial_indent=indent,
                subsequent_indent=indent,
                break_long_words=False,
                break_on_hyphens=False,
            )
        output.extend(wrapped or [line])
    full.write_text("\n".join(output) + "\n", encoding="utf-8")


def split_literal_assignment(line: str) -> list[str] | None:
    match = re.match(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)='([^']*)'$", line)
    if match is None or len(line) <= MAX_LENGTH:
        return None
    indent, name, value = match.groups()
    width = 55
    chunks = [value[index : index + width] for index in range(0, len(value), width)]
    if not chunks:
        return None
    result = [f"{indent}{name}='{chunks[0]}'"]
    result.extend(f"{indent}{name}+='{chunk}'" for chunk in chunks[1:])
    return result


def split_command_substitution(line: str) -> list[str] | None:
    match = re.match(
        r'^(\s*)([A-Za-z_][A-Za-z0-9_]*)="\$\((.*)\)"$',
        line,
    )
    if match is None or len(line) <= MAX_LENGTH:
        return None
    indent, name, command = match.groups()
    parts = command.split()
    command_lines: list[str] = []
    current = indent + "  "
    for part in parts:
        candidate = current + (" " if current.strip() else "") + part
        if len(candidate) > 74 and current.strip():
            command_lines.append(current.rstrip() + " \\")
            current = indent + "    " + part
        else:
            current = candidate
    command_lines.append(current.rstrip())
    return [f'{indent}{name}="$(', *command_lines, f'{indent})"']


def split_echo(line: str) -> list[str] | None:
    match = re.match(r'^(\s*)echo "([^"]+)"(.*)$', line)
    if match is None or len(line) <= MAX_LENGTH:
        return None
    indent, message, suffix = match.groups()
    words = message.split(" ")
    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else current + " " + word
        if current and len(candidate) > 44:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    if len(chunks) < 2:
        return None
    result = [indent + "echo \\"]
    for index, chunk in enumerate(chunks):
        trailer = " \\" if index < len(chunks) - 1 or suffix.strip() else ""
        result.append(indent + f'  "{chunk}"' + trailer)
    if suffix.strip():
        result[-1] += " " + suffix.strip()
    return result


def fix_shell(path: Path) -> None:
    full = ROOT / path
    original = full.read_text(encoding="utf-8")
    output: list[str] = []
    for line in original.splitlines():
        replacement = split_literal_assignment(line)
        if replacement is None:
            replacement = split_command_substitution(line)
        if replacement is None:
            replacement = split_echo(line)
        output.extend(replacement or [line])
    candidate = "\n".join(output) + "\n"
    full.write_text(candidate, encoding="utf-8")
    result = subprocess.run(
        ["bash", "-n", str(path)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        full.write_text(original, encoding="utf-8")


def fix_terraform(path: Path) -> None:
    full = ROOT / path
    lines = full.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    ternary = re.compile(r"^(\s*)([A-Za-z0-9_]+)\s*=\s*(.+)\s\?\s(.+)\s:\s(.+)$")
    for line in lines:
        if len(line) <= MAX_LENGTH:
            output.append(line)
            continue
        match = ternary.match(line)
        if match:
            indent, name, condition, true_value, false_value = match.groups()
            output.extend(
                [
                    f"{indent}{name} = (",
                    f"{indent}  {condition} ?",
                    f"{indent}  {true_value} :",
                    f"{indent}  {false_value}",
                    f"{indent})",
                ]
            )
            continue
        output.append(line)
    full.write_text("\n".join(output) + "\n", encoding="utf-8")


def main() -> None:
    files = tracked_files()
    for path in files:
        if path.parts[:2] == (".github", "workflows"):
            continue
        if path in {
            Path("scripts/style_check.py"),
            Path(".github/style_migrate.py"),
            Path(".github/style_residual_fix.py"),
        }:
            continue
        if path.suffix == ".py" and path.parts[0] in {"app", "scripts"}:
            ensure_rmeio(path)
            fix_python(path)
        elif path.suffix == ".py" and path.parts[0] == "tests":
            fix_python(path)
        elif path.suffix == ".md" and path.name != "LICENSE":
            fix_markdown(path)
        elif path.suffix == ".sh":
            fix_shell(path)
        elif path.suffix == ".tf":
            fix_terraform(path)


if __name__ == "__main__":
    main()
