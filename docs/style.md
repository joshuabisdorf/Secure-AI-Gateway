# Secure AI Gateway style standard

This document defines the source and documentation style for Secure AI Gateway.
It is a project-authored standard. It does not incorporate or require an
external style guide, formatter, or style linter.

The standard does not change the project's Apache-2.0 license, copyright, NOTICE
attribution, or authorship. Style conformance is a repository quality
requirement, not a transfer or sharing of project credit.

## Priorities

Code and documentation should optimize for, in order:

1. security-relevant correctness;
1. readability during review and incident response;
1. explicit contracts and side effects;
1. consistency across the repository;
1. compactness only when it does not reduce clarity.

A shorter expression is not preferred when a slightly longer form makes a
security boundary, failure path, mutation, or invariant easier to inspect.

## Hard line-length rule

Text lines must not exceed 80 characters.

The only exception is content that cannot be split sensibly without changing its
meaning or making it materially harder to use. Examples include:

- a single URL that is itself longer than 80 characters;
- a cryptographic digest or other opaque identifier;
- a generated dependency locator or machine-oriented token;
- a literal test vector whose exact bytes are significant.

The exception applies only to the unsplittable content. It is not permission to
place ordinary prose, comments, arguments, or expressions on a long line. A
label plus a URL should normally be split when the label is what pushes the line
past 80 characters.

The repository-owned style checker enforces this rule.

## General text rules

Tracked text files must:

- use UTF-8;
- use LF line endings;
- end with one newline;
- contain no trailing whitespace;
- avoid tab characters except where a file format requires them;
- keep ordinary lines at or below 80 characters.

`Makefile` recipe indentation is the intentional tab exception.

Legal text that must remain verbatim may be exempt from line wrapping. The
checker treats `LICENSE` as such an artifact.

## Python

Python uses four-space indentation. Tabs are not permitted.

Names use these repository conventions:

- modules and functions use `lower_snake_case`;
- classes and exception types use `UpperCamelCase`;
- constants use `UPPER_SNAKE_CASE` when they are true module-level constants;
- private implementation names begin with one underscore when useful;
- dunder names are reserved for language-defined behavior.

Imports should be easy to scan. Avoid wildcard imports. Prefer explicit names
and keep import-time side effects out of modules.

Public and security-relevant interfaces should use type annotations. Types
should clarify contracts rather than duplicate obvious implementation detail.

Exceptions should be specific. Bare `except` clauses are prohibited. Error
messages must not disclose credentials, prompts, execution tickets, raw PII, or
connection strings.

Boolean conditions should favor named intermediate values when a compound
expression would otherwise obscure policy or failure semantics.

### RMEIO function contracts

RMEIO remains a required project convention. It is not replaced by a generic
argument/return docstring format.

Project functions under `app/` and Python utilities under `scripts/` document
these sections in this order:

1. `Requires`
1. `Modifies`
1. `Effects`
1. `Inputs`
1. `Outputs`

The sections describe the contract, not a line-by-line implementation trace. Use
`Nothing.` or `None.` when a section has no meaningful entry.

`Requires` states preconditions and environmental assumptions.

`Modifies` identifies persistent, process, filesystem, network, or external
state that may change.

`Effects` states externally observable behavior, including failure behavior.

`Inputs` describes parameters and implicit inputs such as environment values
when those inputs materially affect behavior.

`Outputs` describes return values or produced artifacts.

Very small language-protocol methods such as `__str__` may use a concise
ordinary docstring when a full RMEIO contract would add no information. Tests
are not required to carry RMEIO sections, but reusable test utilities should
still document non-obvious state changes.

### Python layout

Use one statement per line. Prefer parentheses for multi-line expressions.
Continuation lines should expose structure rather than align large blocks of
text for visual decoration.

Top-level functions and classes should be visually separated. Keep related
private helpers near the public operation they support when that improves
reviewability.

Comments explain intent, invariants, security rationale, or non-obvious
constraints. They should not narrate syntax already visible in the code.

## Shell

Repository shell programs use Bash explicitly:

```bash
#!/usr/bin/env bash
set -euo pipefail
```

Shell code must quote variable expansions unless intentional word splitting or
glob expansion is the point of the expression. Temporary files containing
secrets must use restrictive permissions and deterministic cleanup.

Functions use `lower_snake_case`. Environment variables and script-level
constants use `UPPER_SNAKE_CASE`. Function-local variables should use `local`
where Bash supports it.

Long commands should be split with one logical argument or argument group per
line. Pipelines should remain readable from left to right and preserve the
failure behavior required by `pipefail`.

## YAML and JSON

YAML and hand-maintained JSON use spaces, never tabs. YAML indentation is two
spaces. Keep mapping structure shallow when a helper file or explicit object
would make security configuration easier to audit.

Generated or machine-oriented JSON may retain its generated layout when
rewriting it would reduce reproducibility or create noisy diffs.

Secrets must never be committed merely to make an example self-contained.
Examples use inert placeholders or runtime secret references.

## Terraform

Terraform configuration should keep one resource concern per logical block and
use descriptive local names. Security-sensitive defaults must be conservative.
Variables and outputs require descriptions when their meaning is not obvious
from the name alone.

The existing Terraform format and validation gate remains authoritative for
Terraform syntax. Project-specific review still applies the 80-character rule
where a line can be sensibly split.

## Markdown and documentation

Prose is wrapped at 80 characters. Headings are concise and descriptive.
Paragraphs should explain one coherent idea. Use lists for true collections, not
as a substitute for connected explanation.

Code blocks preserve the syntax and layout required by the demonstrated tool.
Long opaque values inside code blocks may use the unsplittable exception.

Links should use descriptive labels when prose surrounds them. A raw URL is
acceptable when the URL itself is the object being documented or copied.

Documentation must distinguish verified behavior from reference architecture,
planned work, and optional paid deployment paths.

## Tests

Test names should state the behavior or invariant being verified. Tests should
be deterministic unless their purpose is explicitly to verify concurrency, time,
or randomized input handling.

Security regression tests should assert the failure mode, not merely that an
exception occurred. Secret-bearing test fixtures must use inert values and must
not be copied from real credentials.

Avoid sleeps when a bounded readiness or synchronization condition can be
observed directly.

## Security comments and suppressions

A security-tool suppression must be narrow and adjacent to the suppressed
operation. The accompanying comment must explain why the reported pattern is
safe in this specific design.

Do not suppress a finding merely to obtain a green build. If the invariant that
justifies a suppression changes, remove or re-evaluate the suppression.

## Automated enforcement

Run:

```bash
make style
```

The command executes `scripts/style_check.py`, a repository-owned checker that
uses the Python standard library and Git metadata. It does not call a
third-party style formatter or linter.

The checker enforces objective repository invariants, including:

- the 80-character hard line limit and narrow unsplittable exceptions;
- whitespace, newline, and tab rules;
- Python syntax and naming rules;
- wildcard-import and bare-except rejection;
- RMEIO contracts for project Python functions;
- Bash shebang and strict-mode requirements.

Some rules remain review rules because a machine cannot reliably determine
whether a construct is the clearest or safest expression of intent.

CI runs the style checker independently so style failures have a distinct
failure domain.

## Changes to this standard

Style-rule changes should be deliberate repository changes. A rule should be
changed because it improves maintainability, security review, or consistency,
not because existing code happens to violate it.

When a rule would force a less readable or less correct result, document the
specific exception rather than silently weakening the rule for the whole
repository.
