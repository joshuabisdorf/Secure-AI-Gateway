import argparse

from app.security_policy import SecurityPolicyUnavailable, load_security_policy_registry


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the configured Secure AI Gateway security policy registry."
    )
    parser.add_argument(
        "command",
        choices=("validate",),
        help="Policy operation to perform.",
    )
    return parser


def main() -> None:
    """
    RME

    Requires:
        - SAG_SECURITY_POLICY_FILE identifies the unified JSON security-policy registry.

    Modifies:
        - Terminal output.

    Effects:
        - Validates the complete policy registry without printing policy contents.
        - Exits nonzero when configuration cannot be used safely.

    Inputs:
        - Command-line arguments and SAG_SECURITY_POLICY_FILE.

    Outputs:
        - Concise validation metadata containing only version/profile/client counts.
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
