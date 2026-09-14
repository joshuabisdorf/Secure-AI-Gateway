from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path

POD_IDENTITY_AGENT_IPV4 = "169.254.170.23/32"


def parse_cidrs_json(value: str, label: str) -> list[str]:
    """Parse and canonicalize a JSON array of IPv4 CIDR strings.

    Requires:
        value is intended to contain a JSON array and label identifies the source.
    Modifies:
        Nothing.
    Effects:
        Parses JSON and raises ValueError for empty, duplicate, non-string, or
        non-IPv4 CIDRs.
    Inputs:
        value: JSON text containing CIDR strings.
        label: Human-readable argument name used in validation errors.
    Outputs:
        Sorted canonical IPv4 CIDR strings.
    """
    decoded = json.loads(value)
    if not isinstance(decoded, list) or not decoded:
        raise ValueError(f"{label} must be a non-empty JSON array")

    networks: list[str] = []
    for item in decoded:
        if not isinstance(item, str):
            raise ValueError(f"{label} contains a non-string CIDR")
        network = ipaddress.ip_network(item, strict=True)
        if network.version != 4:
            raise ValueError(f"{label} must contain IPv4 CIDRs only: {item}")
        networks.append(str(network))

    if len(set(networks)) != len(networks):
        raise ValueError(f"{label} contains duplicate CIDRs")
    return sorted(networks)


def ip_block_list(cidrs: list[str], indentation: int) -> str:
    """Render a NetworkPolicy `to` list for IPv4 CIDRs.

    Requires:
        cidrs contains validated canonical IPv4 networks and indentation is
        non-negative.
    Modifies:
        Nothing.
    Effects:
        Performs no I/O.
    Inputs:
        cidrs: Canonical IPv4 CIDR strings.
        indentation: Number of spaces before each list item.
    Outputs:
        YAML fragment containing one ipBlock item per CIDR.
    """
    prefix = " " * indentation
    child = " " * (indentation + 4)
    return "\n".join(
        f"{prefix}- ipBlock:\n{child}cidr: {cidr}" for cidr in cidrs
    )


def render_policies(private_cidrs: list[str], data_cidrs: list[str]) -> str:
    """Render cloud runtime egress NetworkPolicies from real subnet CIDRs.

    Requires:
        private_cidrs and data_cidrs contain validated non-empty IPv4 networks.
    Modifies:
        Nothing.
    Effects:
        Performs no I/O.
    Inputs:
        private_cidrs: EKS worker/private subnet CIDRs from Terraform outputs.
        data_cidrs: Isolated RDS/ElastiCache subnet CIDRs from Terraform outputs.
    Outputs:
        Multi-document Kubernetes YAML for gateway, migration, and Prometheus
        runtime egress.
    """
    private_blocks = ip_block_list(private_cidrs, 8)
    data_blocks = ip_block_list(data_cidrs, 8)
    return f"""apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: sag-cloud-gateway-runtime-egress
  namespace: secure-ai-gateway
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/component: gateway
  policyTypes:
    - Egress
  egress:
    - to:
{data_blocks}
      ports:
        - protocol: TCP
          port: 5432
        - protocol: TCP
          port: 6379
    - to:
{private_blocks}
      ports:
        - protocol: TCP
          port: 443
    - to:
        - ipBlock:
            cidr: {POD_IDENTITY_AGENT_IPV4}
      ports:
        - protocol: TCP
          port: 80
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: sag-cloud-migration-runtime-egress
  namespace: secure-ai-gateway
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/component: migration
  policyTypes:
    - Egress
  egress:
    - to:
{data_blocks}
      ports:
        - protocol: TCP
          port: 5432
    - to:
{private_blocks}
      ports:
        - protocol: TCP
          port: 443
    - to:
        - ipBlock:
            cidr: {POD_IDENTITY_AGENT_IPV4}
      ports:
        - protocol: TCP
          port: 80
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: sag-cloud-prometheus-apiserver-egress
  namespace: secure-ai-gateway
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/component: prometheus
  policyTypes:
    - Egress
  egress:
    - to:
{private_blocks}
      ports:
        - protocol: TCP
          port: 443
"""


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for cloud NetworkPolicy rendering.

    Requires:
        argparse is available from the Python standard library.
    Modifies:
        Nothing outside the newly created parser object.
    Effects:
        Performs no I/O.
    Inputs:
        None.
    Outputs:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        description="Render Secure AI Gateway cloud NetworkPolicies from Terraform CIDRs."
    )
    parser.add_argument("--private-cidrs-json", required=True)
    parser.add_argument("--data-cidrs-json", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    """Render validated cloud NetworkPolicies to the requested output path.

    Requires:
        CLI inputs contain real Terraform subnet CIDR JSON arrays and the output
        parent directory exists.
    Modifies:
        Replaces the requested output file when it already exists.
    Effects:
        Parses command-line arguments, validates CIDRs, and writes UTF-8 YAML.
    Inputs:
        Command-line arguments parsed by build_parser().
    Outputs:
        Process exit code 0 and the rendered YAML file at --output.
    """
    args = build_parser().parse_args()
    private_cidrs = parse_cidrs_json(args.private_cidrs_json, "private CIDRs")
    data_cidrs = parse_cidrs_json(args.data_cidrs_json, "data CIDRs")
    args.output.write_text(
        render_policies(private_cidrs, data_cidrs), encoding="utf-8"
    )
    print(
        "cloud_network_policy=rendered "
        f"private_cidrs={len(private_cidrs)} data_cidrs={len(data_cidrs)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
