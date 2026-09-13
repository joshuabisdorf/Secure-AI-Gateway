import stat

import pytest

from app.api_keys import (
    format_client_record,
    generate_api_key,
    hash_api_key,
    managed_api_key_secret_file,
    open_api_key_secret_file,
    parse_client_records,
    parse_key_id,
    write_api_key_secret,
)


def test_generate_api_key_creates_hash_only_server_record() -> None:
    """
    RME

    Requires:
        - Secure random key generation is available from the operating system.

    Modifies:
        - Nothing outside cryptographic random generator state.

    Effects:
        - Generates a client API key and validates its server-side record.

    Inputs:
        - None.

    Outputs:
        - None. Assertions verify key format and hashed storage behavior.
    """
    api_key, record = generate_api_key("client-a")
    serialized_record = format_client_record(record)

    assert api_key.startswith(f"sag_{record.key_id}_")
    assert parse_key_id(api_key) == record.key_id
    assert record.api_key_sha256 == hash_api_key(api_key)
    assert api_key not in serialized_record
    assert serialized_record.startswith(f"client-a:{record.key_id}:")


def test_parse_client_records_supports_multiple_clients() -> None:
    """
    RME

    Requires:
        - Client records use the documented client_id:key_id:sha256 format.

    Modifies:
        - Nothing.

    Effects:
        - Parses multiple hashed client-key records into a key-ID lookup.

    Inputs:
        - None.

    Outputs:
        - None. Assertions verify both identities are preserved.
    """
    first_hash = "a" * 64
    second_hash = "b" * 64
    records = parse_client_records(
        f"client-a:keya:{first_hash},client-b:keyb:{second_hash}"
    )

    assert records["keya"].client_id == "client-a"
    assert records["keyb"].client_id == "client-b"


def test_parse_client_records_rejects_duplicate_key_ids() -> None:
    """
    RME

    Requires:
        - Duplicate public key identifiers may appear in candidate configuration.

    Modifies:
        - Nothing.

    Effects:
        - Verifies ambiguous key lookup configuration is rejected.

    Inputs:
        - None.

    Outputs:
        - None. The expected ValueError determines whether the test passes.
    """
    with pytest.raises(ValueError, match="duplicate_key_id"):
        parse_client_records(
            f"client-a:keya:{'a' * 64},client-b:keya:{'b' * 64}"
        )


def test_api_key_secret_file_is_owner_only_and_exclusive(tmp_path) -> None:
    """
    RME

    Requires:
        - tmp_path identifies a writable test directory.

    Modifies:
        - Creates one temporary API-key secret file.

    Effects:
        - Verifies raw key delivery uses mode 0600.
        - Verifies an existing destination cannot be overwritten.

    Inputs:
        - tmp_path: pytest temporary-directory fixture.

    Outputs:
        - None. Filesystem assertions determine whether the test passes.
    """
    secret_path = tmp_path / "client-api-key"
    with managed_api_key_secret_file(secret_path) as secret_file:
        write_api_key_secret(secret_file, "sag_keya_test-secret")

    assert secret_path.read_text(encoding="utf-8") == "sag_keya_test-secret\n"
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600

    with pytest.raises(FileExistsError):
        open_api_key_secret_file(secret_path)


def test_api_key_secret_file_is_removed_when_delivery_fails(tmp_path) -> None:
    """
    RME

    Requires:
        - tmp_path identifies a writable test directory.

    Modifies:
        - Creates and then removes one temporary API-key secret file.

    Effects:
        - Verifies failed provisioning does not leave a partial raw credential on disk.

    Inputs:
        - tmp_path: pytest temporary-directory fixture.

    Outputs:
        - None. Filesystem assertions determine whether the test passes.
    """
    secret_path = tmp_path / "failed-client-api-key"

    with pytest.raises(RuntimeError, match="provisioning_failed"):
        with managed_api_key_secret_file(secret_path) as secret_file:
            write_api_key_secret(secret_file, "sag_keya_test-secret")
            raise RuntimeError("provisioning_failed")

    assert not secret_path.exists()
