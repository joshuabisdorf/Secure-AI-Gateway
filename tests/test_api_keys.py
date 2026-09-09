import pytest

from app.api_keys import (
    format_client_record,
    generate_api_key,
    hash_api_key,
    parse_client_records,
    parse_key_id,
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
