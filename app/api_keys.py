import argparse
import hashlib
import os
import re
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, TextIO

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
        - Gateway API keys are generated with at least 256 bits of CSPRNG entropy.

    Modifies:
        - Nothing.

    Effects:
        - Computes a one-way SHA-256 digest for high-entropy API-key storage.
        - Does not use SHA-256 as a human-password hashing function.

    Inputs:
        - api_key: Raw gateway API key.

    Outputs:
        - Lowercase hexadecimal SHA-256 digest.
    """
    # Gateway keys contain 256 bits of CSPRNG entropy. A password KDF is not
    # required for offline resistance to guessing this uniformly random secret.
    # codeql[py/weak-sensitive-data-hashing]
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


def open_api_key_secret_file(path: str | os.PathLike[str]) -> TextIO:
    """
    RME

    Requires:
        - path identifies a new file in an existing writable directory.

    Modifies:
        - Creates the requested filesystem path with owner-only permissions.

    Effects:
        - Opens a new API-key delivery file without following or overwriting an existing path.
        - Uses mode 0600 so only the creating user can read or write the file.

    Inputs:
        - path: Destination path for one raw API key.

    Outputs:
        - Writable UTF-8 text stream for the newly created secret file.

    Raises:
        - FileExistsError: The destination already exists, including a pre-existing symlink.
        - OSError: The file cannot be created securely.
    """
    secret_path = Path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = os.open(secret_path, flags, 0o600)
    try:
        return os.fdopen(fd, "w", encoding="utf-8", newline="\n")
    except Exception:
        os.close(fd)
        secret_path.unlink(missing_ok=True)
        raise


def write_api_key_secret(secret_file: TextIO, api_key: str) -> None:
    """
    RME

    Requires:
        - secret_file is an open writable stream created for API-key delivery.
        - api_key is the complete raw gateway bearer credential.

    Modifies:
        - Replaces the contents of secret_file and synchronizes them to storage.

    Effects:
        - Writes exactly one API key plus a trailing newline.
        - Truncates any previous candidate key before returning.

    Inputs:
        - secret_file: Secure destination stream.
        - api_key: Raw API key to deliver.

    Outputs:
        - None.
    """
    secret_file.seek(0)
    secret_file.write(f"{api_key}\n")
    secret_file.truncate()
    secret_file.flush()
    os.fsync(secret_file.fileno())


@contextmanager
def managed_api_key_secret_file(path: str | os.PathLike[str]) -> Iterator[TextIO]:
    """
    RME

    Requires:
        - path satisfies open_api_key_secret_file requirements.

    Modifies:
        - Creates a 0600 secret file and removes it when the protected operation fails.

    Effects:
        - Keeps a successfully written secret file after normal completion.
        - Removes partial or stale secret output when an exception escapes the context.

    Inputs:
        - path: Destination path for API-key delivery.

    Outputs:
        - Context-managed writable secret stream.
    """
    secret_path = Path(path)
    secret_file = open_api_key_secret_file(secret_path)
    try:
        yield secret_file
    except BaseException:
        secret_file.close()
        secret_path.unlink(missing_ok=True)
        raise
    else:
        secret_file.close()


def main() -> None:
    """
    RME

    Requires:
        - A client ID and explicit API-key output path are supplied on the command line.

    Modifies:
        - Creates the requested 0600 API-key output file.
        - Writes non-secret metadata to standard output.

    Effects:
        - Generates a gateway API key without printing the raw credential.
        - Writes the raw key once to an exclusive owner-only file.
        - Prints the hashed server configuration record separately.

    Inputs:
        - Command-line client ID and --api-key-file path.

    Outputs:
        - Raw client API key in the requested secret file.
        - Client identity, secret-file path, and server record on standard output.
    """
    parser = argparse.ArgumentParser(
        description="Generate a Secure AI Gateway client API key."
    )
    parser.add_argument("client_id")
    parser.add_argument(
        "--api-key-file",
        required=True,
        help="New owner-only file that will receive the raw API key.",
    )
    args = parser.parse_args()

    try:
        with managed_api_key_secret_file(args.api_key_file) as secret_file:
            api_key, record = generate_api_key(args.client_id)
            write_api_key_secret(secret_file, api_key)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    print(f"Client ID: {record.client_id}")
    print(f"API key file: {args.api_key_file}")
    print(f"Server record: {format_client_record(record)}")


if __name__ == "__main__":
    main()
