import argparse
import hashlib
import re
import secrets
from dataclasses import dataclass

_client_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_key_id_pattern = re.compile(r"^[A-Za-z0-9-]{4,32}$")
_sha256_pattern = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ClientKeyRecord:
    client_id: str
    key_id: str
    api_key_sha256: str


@dataclass(frozen=True)
class Principal:
    client_id: str
    key_id: str


def hash_api_key(api_key: str) -> str:
    """
    RME

    Requires:
        - api_key is the complete gateway bearer credential.

    Modifies:
        - Nothing.

    Effects:
        - Computes a one-way SHA-256 digest for high-entropy API-key storage.

    Inputs:
        - api_key: Raw gateway API key.

    Outputs:
        - Lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def parse_key_id(api_key: str) -> str | None:
    """
    RME

    Requires:
        - api_key may contain a structured gateway API key.

    Modifies:
        - Nothing.

    Effects:
        - Validates the public key identifier embedded in the bearer credential.

    Inputs:
        - api_key: Candidate gateway API key.

    Outputs:
        - The key ID when the key format is valid; otherwise None.
    """
    if not api_key.startswith("sag_"):
        return None

    parts = api_key.split("_", 2)
    if len(parts) != 3:
        return None

    _, key_id, secret = parts
    if not _key_id_pattern.fullmatch(key_id) or not secret:
        return None

    return key_id


def parse_client_records(configured_clients: str) -> dict[str, ClientKeyRecord]:
    """
    RME

    Requires:
        - configured_clients uses client_id:key_id:sha256 records separated by commas.

    Modifies:
        - Nothing.

    Effects:
        - Validates client identity and API-key hash configuration.
        - Rejects malformed or duplicate key identifiers.

    Inputs:
        - configured_clients: Serialized client-key records.

    Outputs:
        - Mapping from key ID to validated client-key record.

    Raises:
        - ValueError: The configuration is empty, malformed, or contains duplicates.
    """
    records: dict[str, ClientKeyRecord] = {}

    for raw_record in configured_clients.split(","):
        raw_record = raw_record.strip()
        if not raw_record:
            continue

        parts = raw_record.split(":")
        if len(parts) != 3:
            raise ValueError("invalid_client_record")

        client_id, key_id, api_key_sha256 = (part.strip() for part in parts)

        if not _client_id_pattern.fullmatch(client_id):
            raise ValueError("invalid_client_id")
        if not _key_id_pattern.fullmatch(key_id):
            raise ValueError("invalid_key_id")
        if not _sha256_pattern.fullmatch(api_key_sha256):
            raise ValueError("invalid_api_key_hash")
        if key_id in records:
            raise ValueError("duplicate_key_id")

        records[key_id] = ClientKeyRecord(
            client_id=client_id,
            key_id=key_id,
            api_key_sha256=api_key_sha256,
        )

    if not records:
        raise ValueError("no_client_records")

    return records


def generate_api_key(client_id: str) -> tuple[str, ClientKeyRecord]:
    """
    RME

    Requires:
        - client_id is 1-64 characters using letters, digits, dot, underscore, or hyphen.

    Modifies:
        - Cryptographic random generator state maintained by the operating system.

    Effects:
        - Generates a new high-entropy structured gateway API key.
        - Produces a server-side record containing only the key hash.

    Inputs:
        - client_id: Stable identity assigned to the gateway client.

    Outputs:
        - Tuple containing the raw API key and its hashed client-key record.
    """
    if not _client_id_pattern.fullmatch(client_id):
        raise ValueError("invalid_client_id")

    key_id = secrets.token_hex(4)
    api_key = f"sag_{key_id}_{secrets.token_urlsafe(32)}"
    record = ClientKeyRecord(
        client_id=client_id,
        key_id=key_id,
        api_key_sha256=hash_api_key(api_key),
    )
    return api_key, record


def format_client_record(record: ClientKeyRecord) -> str:
    """
    RME

    Requires:
        - record contains a validated client identity, key ID, and API-key digest.

    Modifies:
        - Nothing.

    Effects:
        - Serializes a client-key record for SAG_CLIENTS configuration.

    Inputs:
        - record: Client-key record to serialize.

    Outputs:
        - client_id:key_id:sha256 configuration record.
    """
    return f"{record.client_id}:{record.key_id}:{record.api_key_sha256}"


def main() -> None:
    """
    RME

    Requires:
        - A client ID is supplied on the command line.

    Modifies:
        - Nothing outside terminal output.

    Effects:
        - Generates a gateway API key and prints the raw client key once.
        - Prints the hashed server configuration record separately.

    Inputs:
        - Command-line client ID.

    Outputs:
        - Client API key and server record written to standard output.
    """
    parser = argparse.ArgumentParser(
        description="Generate a Secure AI Gateway client API key."
    )
    parser.add_argument("client_id")
    args = parser.parse_args()

    try:
        api_key, record = generate_api_key(args.client_id)
    except ValueError as exc:
        parser.error(str(exc))

    print(f"Client ID: {record.client_id}")
    print(f"API key: {api_key}")
    print(f"Server record: {format_client_record(record)}")


if __name__ == "__main__":
    main()
