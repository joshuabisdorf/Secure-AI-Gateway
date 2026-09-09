CREATE TABLE IF NOT EXISTS gateway_clients (
    client_id VARCHAR(64) PRIMARY KEY,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gateway_api_keys (
    key_id VARCHAR(32) PRIMARY KEY,
    client_id VARCHAR(64) NOT NULL REFERENCES gateway_clients(client_id) ON DELETE CASCADE,
    api_key_sha256 CHAR(64) NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ NULL,
    CONSTRAINT gateway_api_keys_hash_hex CHECK (api_key_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS idx_gateway_api_keys_client_id
    ON gateway_api_keys (client_id);
