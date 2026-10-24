# Secure AI Gateway style standard

This document defines the project's own source and documentation style.
The repository uses its own coding rules and Sphinx-style Python docstrings;
it does not vendor an external formatter or general lint policy.

## Goals

The standard exists to make security-sensitive code predictable to read,
review, and maintain. Rules favor explicit behavior, small reviewable changes,
and machine-checkable invariants over cosmetic preferences.

## Universal rules

- Text files use UTF-8, LF line endings, and one final newline.
- Trailing whitespace is prohibited.
- Indentation uses spaces except where a file format requires tabs, such as
  Makefile recipes.
- Lines are at most 80 characters unless splitting the line would make it less
  correct or less readable.
- Legitimate line-length exceptions include indivisible URLs, cryptographic
  material, immutable action references, generated dependency records, and
  other atomic values that cannot be sensibly split.
- Long prose, comments, expressions, argument lists, shell commands, YAML, and
  Terraform declarations must be wrapped when they can be split cleanly.
- Names should describe purpose rather than implementation history.
- Comments explain constraints, security rationale, or non-obvious decisions;
  they should not merely restate the code.
- Dead code, commented-out code, and speculative configuration do not belong in
  the maintained tree.

## Python

Python code uses four-space indentation and type annotations for public module
interfaces when a useful type can be stated without obscuring the code.

Imports are grouped as standard library, third-party packages, then project
imports, with blank lines between groups. Wildcard imports are prohibited.
Avoid multiple unrelated imports in one `import` statement.

Functions should be focused and should return explicit values rather than
mutating hidden global state. Mutable default arguments are prohibited.
Bare `except` clauses are prohibited. Exceptions should be caught at the
narrowest useful boundary and should preserve fail-closed behavior for
security-critical dependencies.

Module constants use `UPPER_SNAKE_CASE`. Functions, methods, variables, and
modules use `snake_case`. Classes use `CapWords`. Private implementation names
begin with one leading underscore when they are intentionally non-public.

The project-owned style checker enforces module, function, method, and class
naming where the rule can be validated safely from Python syntax.

## Python documentation: Sphinx-style docstrings

Python uses native Sphinx/reStructuredText docstrings. Start with a concise
imperative summary, then add the applicable Sphinx Python-domain fields:

- `:param name:` describes a parameter.
- `:type name:` documents a type when a Python annotation is unavailable.
- `:returns:` describes a meaningful returned value.
- `:rtype:` supplies the return type if it is not already annotated.
- `:raises ExceptionType:` states why a significant exception is raised.
- `.. note::` explains security invariants, preconditions, side effects,
  credential handling, and fail-closed behavior when relevant.

Write each field on its own line. Keep parameter fields before return and
exception fields, followed by note directives. Omit inapplicable fields.
Do not duplicate type hints or use Google-style `Args:`/`Returns:`/`Notes:`
headings. Short pure helpers may use a single summary line.

Example:

```python
def hash_api_key(api_key: str) -> str:
    """Compute a digest for a high-entropy gateway API key.

    :param api_key: Complete bearer credential to hash.
    :returns: Lowercase hexadecimal SHA-256 digest.

    .. note::

        - Intended for randomly generated API keys, not human passwords.
        - Raw credentials must not be persisted.
    """
```

This is Sphinx's native Python-domain field-list syntax, suitable for
`sphinx.ext.autodoc`; no Google/Napoleon docstring conversion is required.

## Tests

Tests should have one primary behavioral assertion or closely related group of
assertions. Test names describe the condition and expected behavior. Security
regressions should preserve the concrete invariant being defended rather than
only checking an implementation detail.

Tests may use compact helper functions without extensive docstrings because
they are verification code rather than runtime project interfaces.

## Shell

Repository shell scripts use `#!/usr/bin/env bash` and enable
`set -euo pipefail` near the beginning of the script. Variables are quoted
unless deliberate word splitting is required. Temporary files and secrets are
cleaned up with traps when failure could otherwise leave sensitive material
behind.

Long commands are split with backslash continuations at semantic boundaries.
Any intentional exception to strict failure behavior should be documented next
to the command.

## Configuration and infrastructure

YAML, JSON, TOML, Docker, SQL, and Terraform should prefer explicit values over
implicit defaults when the default affects security, availability, cost, or
operator expectations. Generated files and lock files are not manually
reformatted solely to satisfy presentation rules.

Terraform is kept in the canonical formatting produced by the repository's
existing Terraform workflow. Kubernetes manifests use two-space indentation.
Secret values must not be committed to tracked configuration.

## Documentation

Markdown prose is wrapped to 80 characters. Tables, URLs, command output,
identifiers, and code blocks may exceed 80 characters only when splitting them
would damage their meaning or usability.

Documentation must distinguish implemented and verified behavior from planned,
reference-only, or optional behavior.

## Enforcement

`scripts/style_check.py` is the repository-owned checker for rules that
can be validated safely without importing a third-party formatting or
linting policy.

The checker evaluates the entire tracked repository on every run. There
is no legacy baseline, moving exemption, or grandfathered file set.
Existing and newly modified project files are held to the same standard.

Run the whole-repository gate with:

```bash
make style
```

CI runs the same whole-repository command. Machine-enforced checks include
text hygiene, the 80-character limit, Python syntax and structural
hazards, Python naming, Sphinx-style docstring presence and field order,
and Bash shebang and strict-mode requirements.

The checker is intentionally conservative: it rejects clear violations
and leaves subjective review decisions to code review rather than
rewriting source automatically.

A line-length exception should make its necessity obvious from the line
itself. The checker recognizes only narrow atomic or canonical forms;
new blanket exclusions must not be added merely to make CI pass.
