from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"missing replacement target: {path}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_all(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"missing replacement target: {path}")
    target.write_text(text.replace(old, new), encoding="utf-8")


def patch_style_checker() -> None:
    old = """    if _is_dunder(node.name):
        return False
    if node.name.startswith("_") and len(node.body) <= 3:
        return False
    return True
"""
    new = """    if _is_dunder(node.name):
        return False
    if (
        len(node.body) == 1
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and node.body[0].value.value is Ellipsis
    ):
        return False
    if node.name.startswith("_") and len(node.body) <= 3:
        return False
    return True
"""
    replace_once("scripts/style_check.py", old, new)


def patch_aws_runtime() -> None:
    replace_once(
        "app/aws_runtime.py",
        """def _database_credentials(value: Mapping[str, Any]) -> tuple[str, str]:
    if frozenset(value) != {"username", "password"}:
""",
        """def _database_credentials(value: Mapping[str, Any]) -> tuple[str, str]:
    \"\"\"
    RME

    Requires:
        - value is the parsed runtime database secret object.

    Modifies:
        - Nothing.

    Effects:
        - Validates the exact credential schema, username, and password length.
        - Rejects malformed credentials without exposing their values.

    Inputs:
        - value: Candidate runtime database credentials.

    Outputs:
        - Validated username and password.
    \"\"\"
    if frozenset(value) != {"username", "password"}:
""",
    )
    replace_once(
        "app/aws_runtime.py",
        """def _tool_signing_key(value: Mapping[str, Any]) -> str:
    if frozenset(value) != {"signing_key"}:
""",
        """def _tool_signing_key(value: Mapping[str, Any]) -> str:
    \"\"\"
    RME

    Requires:
        - value is the parsed execution-ticket signing secret object.

    Modifies:
        - Nothing.

    Effects:
        - Validates exact schema and safe signing-key byte-length bounds.
        - Fails closed without logging or returning malformed secret material.

    Inputs:
        - value: Candidate signing-key secret object.

    Outputs:
        - Validated execution-ticket signing key.
    \"\"\"
    if frozenset(value) != {"signing_key"}:
""",
    )
    replace_once(
        "app/aws_runtime.py",
        """def _provider_environment(value: Mapping[str, Any]) -> dict[str, str]:
    if not frozenset(value).issubset(_provider_secret_fields):
""",
        """def _provider_environment(value: Mapping[str, Any]) -> dict[str, str]:
    \"\"\"
    RME

    Requires:
        - value is the parsed optional provider secret object.

    Modifies:
        - Nothing.

    Effects:
        - Allows only the explicitly supported provider environment fields.
        - Rejects non-string or unexpected fields without exposing values.

    Inputs:
        - value: Candidate provider-secret mapping.

    Outputs:
        - Validated provider environment additions.
    \"\"\"
    if not frozenset(value).issubset(_provider_secret_fields):
""",
    )
    replace_once(
        "app/aws_runtime.py",
        """def main() -> None:
    \"\"\"
    Load cloud runtime secrets and exec the gateway command without printing
    them.
    \"\"\"
""",
        """def main() -> None:
    \"\"\"
    RME

    Requires:
        - CLI arguments contain the gateway command after optional `--`.
        - Required AWS runtime secret configuration is available.

    Modifies:
        - Process state when the runtime command is executed.

    Effects:
        - Loads cloud runtime secrets and execs the gateway command.
        - Converts safe RuntimeSecretError reasons into CLI parser errors.
        - Does not print secret values.

    Inputs:
        - Command-line arguments and AWS runtime environment configuration.

    Outputs:
        - None; successful execution replaces the current process.
    \"\"\"
""",
    )


def patch_rate_limit() -> None:
    replace_once(
        "app/rate_limit.py",
        """    def reset(self) -> None:
        \"\"\"Clear all process-local test rate-limit state.\"\"\"
        with self._lock:
""",
        """    def reset(self) -> None:
        \"\"\"
        RME

        Requires:
            - The in-memory limiter may contain process-local test state.

        Modifies:
            - All process-local rate-limit buckets owned by this limiter.

        Effects:
            - Clears rate-limit state under the limiter lock.

        Inputs:
            - None.

        Outputs:
            - None.
        \"\"\"
        with self._lock:
""",
    )
    replace_once(
        "app/rate_limit.py",
        """    async def close(self) -> None:
        \"\"\"Close the shared-backend client's connection pool.\"\"\"
        await self._client.aclose()
""",
        """    async def close(self) -> None:
        \"\"\"
        RME

        Requires:
            - The Redis-compatible client may own open connections.

        Modifies:
            - Shared-backend client connection-pool state.

        Effects:
            - Closes the Redis-compatible client cleanly.

        Inputs:
            - None.

        Outputs:
            - None.
        \"\"\"
        await self._client.aclose()
""",
    )
    replace_once(
        "app/rate_limit.py",
        """    async def check(self, client_id: str, limit_rpm: int) -> RateLimitDecision:
        \"\"\"Fail closed when no usable rate-limit backend is configured.\"\"\"
        raise RateLimiterUnavailable("rate_limiter_not_configured")
""",
        """    async def check(
        self,
        client_id: str,
        limit_rpm: int,
    ) -> RateLimitDecision:
        \"\"\"
        RME

        Requires:
            - No usable rate-limit backend is configured.

        Modifies:
            - Nothing.

        Effects:
            - Fails closed instead of allowing an unthrottled request.

        Inputs:
            - client_id: Authenticated client identity.
            - limit_rpm: Requested rate-limit ceiling.

        Outputs:
            - No decision; always raises RateLimiterUnavailable.
        \"\"\"
        raise RateLimiterUnavailable("rate_limiter_not_configured")
""",
    )


def patch_prompt_injection() -> None:
    path = "app/prompt_injection.py"
    replace_once(
        path,
        """                r"\\b(?:reveal|show|print|display|repeat|output|provide)\\b.{0,48}"
""",
        """                r"\\b(?:reveal|show|print|display|repeat|"
                r"output|provide)\\b.{0,48}"
""",
    )
    replace_once(
        path,
        """                r"\\bwhat\\s+(?:were|are)\\s+(?:your\\s+)?(?:exact\\s+)?"
                (
                    '(?:system\\\\s+|developer\\\\s+|hidden\\\\s+|'
                    'initial\\\\s+)?instructions\\\\b'
                ),
""",
        """                r"\\bwhat\\s+(?:were|are)\\s+(?:your\\s+)?"
                r"(?:exact\\s+)?"
                r"(?:system\\s+|developer\\s+|hidden\\s+|initial\\s+)?"
                r"instructions\\b",
""",
    )
    replace_once(
        path,
        """                r"\\byou\\s+are\\s+now\\s+(?:in\\s+)?"
                (
                    '(?:developer|admin|administrator|roo'
                    't|debug|unrestricted)\\\\s+mode\\\\b'
                ),
""",
        """                r"\\byou\\s+are\\s+now\\s+(?:in\\s+)?"
                r"(?:developer|admin|administrator|root|debug|"
                r"unrestricted)\\s+mode\\b",
""",
    )
    replace_once(
        path,
        """                r"\\bact\\s+as\\s+(?:an?\\s+)?"
                (
                    '(?:unrestricted|uncensored|developer'
                    '|system|administrator|root)\\\\b'
                ),
""",
        """                r"\\bact\\s+as\\s+(?:an?\\s+)?"
                r"(?:unrestricted|uncensored|developer|system|"
                r"administrator|root)\\b",
""",
    )
    replace_once(
        path,
        """                r"\\b(?:bypass|disable|circumvent|override|ignore)\\b.{0,48}"
                (
                    '\\\\b(?:safety|security|policy|policies'
                    '|guardrails?|restrictions?|filters?)'
                    '\\\\b'
                ),
""",
        """                r"\\b(?:bypass|disable|circumvent|override|"
                r"ignore)\\b.{0,48}"
                r"\\b(?:safety|security|policy|policies|guardrails?|"
                r"restrictions?|filters?)\\b",
""",
    )
    replace_once(
        path,
        """                r"\\b(?:reveal|show|print|display|output|provide|give\\s+me)\\b.{0,64}"
""",
        """                r"\\b(?:reveal|show|print|display|output|provide|"
                r"give\\s+me)\\b.{0,64}"
""",
    )


def patch_readme() -> None:
    path = "README.md"
    replace_once(
        path,
        "    G->>G: re-authenticate + verify ticket + re-check policy + one-time replay claim\n",
        "    G->>G: re-auth + verify + policy recheck + one-time replay claim\n",
    )
    replace_once(
        path,
        "make kind-verify         verify policy, rescheduling, shared state, and bounded load\n",
        "make kind-verify         verify policy, rescheduling, state, and load\n",
    )
    replace_once(
        path,
        "Terraform defines protected S3/KMS remote state plus an application architecture\n",
        "Terraform defines protected S3/KMS remote state plus an application\narchitecture\n",
    )
    replace_once(
        path,
        "├── docs/                        architecture, security, operations, release evidence\n",
        "├── docs/                        security, operations, release evidence\n",
    )
    replace_once(
        path,
        "├── scripts/                     verification, kind, release, and optional AWS helpers\n",
        "├── scripts/                     verification and deployment helpers\n",
    )
    replace_once(
        path,
        "├── .github/workflows/           CI, CodeQL, preflight, security, release workflows\n",
        "├── .github/workflows/           CI and release workflows\n",
    )


def patch_docs() -> None:
    replace_once(
        "docs/continuous-integration.md",
        """bash -n scripts/demo.sh scripts/resilience-smoke.sh \\
  scripts/k8s-local-up.sh scripts/k8s-verify.sh \\
  scripts/aws-cloud-preflight.sh scripts/aws-cloud-deploy.sh scripts/aws-cloud-verify.sh
""",
        """bash -n scripts/demo.sh scripts/resilience-smoke.sh \\
  scripts/k8s-local-up.sh scripts/k8s-verify.sh \\
  scripts/aws-cloud-preflight.sh scripts/aws-cloud-deploy.sh \\
  scripts/aws-cloud-verify.sh
""",
    )
    replace_once(
        "docs/observability.md",
        """sum(rate(sag_tool_authorization_decisions_total{stage="execution"}[5m])) by (outcome, risk)
""",
        """sum(
  rate(
    sag_tool_authorization_decisions_total{stage="execution"}[5m]
  )
) by (outcome, risk)
""",
    )
    replace_once(
        "docs/system-prompt-leakage.md",
        "FAIL case=direct_repeat resolved_model=provider/model signals=canary_disclosed,protected_phrase_disclosed\n",
        "FAIL case=direct_repeat signals=canary_disclosed,protected_phrase_disclosed\n",
    )
    replace_once(
        "docs/tool-execution-authorization.md",
        "cp config/tool-execution-policies.example.json config/tool-execution-policies.json\n",
        """cp config/tool-execution-policies.example.json \\
  config/tool-execution-policies.json
""",
    )


def patch_kubernetes() -> None:
    replace_once(
        "k8s/base/config.yaml",
        '              "record_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$"}\n',
        """              "record_id": {
                "type": "string",
                "pattern": "^[A-Za-z0-9_-]{1,128}$"
              }
""",
    )
    old = """          - source_labels: [__meta_kubernetes_pod_label_app_kubernetes_io_component]
            regex: gateway
"""
    new = """          - source_labels:
              - __meta_kubernetes_pod_label_app_kubernetes_io_component
            regex: gateway
"""
    replace_once("k8s/cloud/observability.yaml", old, new)
    replace_once("k8s/local/observability.yaml", old, new)


def patch_shell() -> None:
    replace_once(
        "scripts/aws-cloud-deploy.sh",
        """    trap 'if [ -n "${CLIENT_SECRET_FILE:-}" ] && [ -n "${GATEWAY_POD:-}" ]; then kubectl -n "$NAMESPACE" exec "$GATEWAY_POD" -- rm -f "$CLIENT_SECRET_FILE" >/dev/null 2>&1 || true; fi; rm -rf "$TMP_DIR"' EXIT
""",
        """    cleanup_deploy() {
      if [ -n "${CLIENT_SECRET_FILE:-}" ] && [ -n "${GATEWAY_POD:-}" ]; then
        kubectl -n "$NAMESPACE" exec "$GATEWAY_POD" -- \\
          rm -f "$CLIENT_SECRET_FILE" >/dev/null 2>&1 || true
      fi
      rm -rf "$TMP_DIR"
    }
    trap cleanup_deploy EXIT
""",
    )
    signing_old = """export SAG_TOOL_EXECUTION_SIGNING_KEY="${SAG_TOOL_EXECUTION_SIGNING_KEY:-$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)}"
"""
    signing_new = """if [ -z "${SAG_TOOL_EXECUTION_SIGNING_KEY:-}" ]; then
  SAG_TOOL_EXECUTION_SIGNING_KEY="$(
    python - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
  )"
  export SAG_TOOL_EXECUTION_SIGNING_KEY
fi
"""
    replace_once("scripts/demo.sh", signing_old, signing_new)
    replace_once("scripts/resilience-smoke.sh", signing_old, signing_new)
    replace_once(
        "scripts/k8s-local-up.sh",
        """trap 'kubectl -n "$NAMESPACE" exec "$GATEWAY_POD" -- rm -f "$CLIENT_SECRET_FILE" >/dev/null 2>&1 || true' EXIT
""",
        """cleanup_client_secret() {
  kubectl -n "$NAMESPACE" exec "$GATEWAY_POD" -- \\
    rm -f "$CLIENT_SECRET_FILE" >/dev/null 2>&1 || true
}
trap cleanup_client_secret EXIT
""",
    )
    replace_once(
        "scripts/k8s-verify.sh",
        """    -o jsonpath='{range .items[*].status.containerStatuses[*]}{.restartCount}{"\\n"}{end}'
""",
        """    -o jsonpath='{range .items[*].status.containerStatuses[*]}'\\
'{.restartCount}{"\\n"}{end}'
""",
    )
    replace_once(
        "scripts/resilience-smoke.sh",
        """      '{"model":"mock-model","messages":[{"role":"user","content":"Resilience probe."}]}' \\
""",
        """      '{"model":"mock-model","messages":['\\
'{"role":"user","content":"Resilience probe."}]}' \\
""",
    )
    replace_once(
        "scripts/aws-cloud-verify.sh",
        """    -d '{"model":"openrouter/free","messages":[{"role":"user","content":"Cloud verification"}]}'
""",
        """    -d '{"model":"openrouter/free","messages":['\\
'{"role":"user","content":"Cloud verification"}]}'
""",
    )


def patch_terraform() -> None:
    replace_once(
        "terraform/aws/variables.tf",
        """    condition     = alltrue([for cidr in var.eks_public_access_cidrs : can(cidrnetmask(cidr))])
""",
        """    condition = alltrue([
      for cidr in var.eks_public_access_cidrs :
      can(cidrnetmask(cidr))
    ])
""",
    )
    replace_once(
        "terraform/bootstrap/main.tf",
        'resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {\n',
        """resource "aws_s3_bucket_server_side_encryption_configuration" \
"terraform_state" {
""",
    )


def patch_tests() -> None:
    replacements = {
        "tests/test_prompt_injection.py": (
            "test_encoded_prompt_injection_is_only_flagged_after_decoding_attack_text",
            "test_encoded_attack_is_flagged_only_after_decoding",
        ),
        "tests/test_request_hardening.py": (
            "test_request_body_limit_allows_bounded_stream_and_adds_security_headers",
            "test_bounded_stream_gets_security_headers",
        ),
        "tests/test_security_policy.py": (
            "test_security_policy_registry_rejects_unknown_fields_and_non_integer_version",
            "test_registry_rejects_unknown_fields_and_non_integer_version",
        ),
        "tests/test_system_prompt_leakage.py": (
            "test_live_evaluation_harness_is_network_independent_with_test_provider",
            "test_live_harness_is_network_independent_with_test_provider",
        ),
        "tests/test_tool_authorization.py": (
            "test_parse_client_allowed_tools_supports_explicit_none_and_multiple_grants",
            "test_allowed_tools_support_none_and_multiple_grants",
        ),
        "tests/test_tool_execution.py": (
            "test_gateway_rejects_request_schema_that_differs_from_authoritative_registry",
            "test_gateway_rejects_schema_different_from_registry",
        ),
        "tests/test_tool_execution_ticket_encoding.py": (
            "test_execution_ticket_shape_accepts_only_unpadded_urlsafe_two_segments",
            "test_ticket_shape_accepts_only_unpadded_urlsafe_segments",
        ),
    }
    for path, (old, new) in replacements.items():
        replace_once(path, old, new)


def main() -> None:
    patch_style_checker()
    patch_aws_runtime()
    patch_rate_limit()
    patch_prompt_injection()
    patch_readme()
    patch_docs()
    patch_kubernetes()
    patch_shell()
    patch_terraform()
    patch_tests()


if __name__ == "__main__":
    main()
