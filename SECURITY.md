# Security policy

Secure AI Gateway is a security-focused portfolio project. Security reports are welcome.

## Reporting a vulnerability

Do not publish exploit details, credentials, execution tickets, provider keys, or other sensitive evidence in a public issue.

Use GitHub's private vulnerability-reporting / security-advisory flow for this repository when available from the repository **Security** tab. Include the affected revision, the security boundary involved, minimal reproduction steps, expected versus observed behavior, and the impact you believe is possible.

If private vulnerability reporting is unavailable, open a public issue containing only a request for a private security contact. Do not include the vulnerability details in that issue.

## Scope

Particularly relevant reports include:

- gateway authentication or authorization bypass;
- cross-client data or policy isolation failures;
- API-key, provider-credential, prompt, PII, tool-argument, or execution-ticket leakage;
- execution-ticket forgery, replay, identity confusion, schema substitution, or policy-revocation bypass;
- model/tool output being treated as authority without execution-time mediation;
- request-boundary denial-of-service amplification;
- Redis/PostgreSQL fail-open behavior;
- telemetry or audit paths exposing sensitive content;
- container/Kubernetes privilege escalation caused by repository configuration;
- supply-chain compromise paths in CI or release workflows.

Reports about the optional AWS reference architecture are useful, but that architecture is not required to be deployed and is not claimed to have been live-verified.

## Security guarantees and non-guarantees

The gateway is designed to fail closed for authentication, model/tool authorization, shared rate-limit state, persistent usage accounting, and execution-time tool authorization. Model output is untrusted, and the gateway does not currently execute external side-effecting tools itself.

Prompt-injection and semantic PII detectors are defense-in-depth controls with curated regression datasets; they are not complete classifiers or formal guarantees. System prompts are not treated as secrets or authorization boundaries.

No security review, automated scanner, benchmark, or test suite proves the absence of vulnerabilities.
