import argparse

from app.security_policy import (
    SecurityPolicyUnavailable,
    load_security_policy_registry,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the configured Secure AI Gateway security policy"
            " registry."
        )
    )
    parser.add_argument(
        "command",
        choices=("validate",),
        help="Policy operation to perform.",
    )
    return parser


def main() -> None:
    """Validate the complete policy registry without printing policy contents.

    Returns:
        Concise validation metadata containing only version/profile/client
        counts.

    Notes:
        - Input: Command-line arguments and SAG_SECURITY_POLICY_FILE.
        - Precondition: SAG_SECURITY_POLICY_FILE identifies the unified JSON
          security-policy registry.
        - Side effect: Terminal output.
        - Exits nonzero when configuration cannot be used safely.
    """
    args = _build_parser().parse_args()
    if args.command != "validate":
        raise SystemExit(2)

    try:
        registry = load_security_policy_registry()
    except SecurityPolicyUnavailable as exc:
        print(f"ERROR security_policy reason={exc.reason}")
        raise SystemExit(2) from None

    print(
        "VALID security_policy "
        f"version={registry.version} "
        f"profiles={len(registry.profiles)} clients={len(registry.clients)}"
    )


if __name__ == "__main__":
    main()
