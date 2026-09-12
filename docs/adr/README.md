# Architecture decision records

This directory records security and delivery decisions whose rationale is not obvious from the implementation alone.

- [ADR 0001: Separate tool exposure from execution authorization](0001-execution-time-tool-authorization.md)
- [ADR 0002: Keep the required project path zero-cost](0002-zero-cost-required-path.md)

ADRs describe why a boundary exists and which alternatives were rejected. Current implementation and security guarantees remain authoritative in code, tests, and [`../security-review.md`](../security-review.md).
