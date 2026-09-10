CREATE TABLE IF NOT EXISTS gateway_daily_usage (
    client_id VARCHAR(64) NOT NULL REFERENCES gateway_clients(client_id) ON DELETE CASCADE,
    usage_date DATE NOT NULL,
    tokens_used BIGINT NOT NULL DEFAULT 0,
    cost_used_usd NUMERIC(30, 15) NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (client_id, usage_date),
    CONSTRAINT gateway_daily_usage_tokens_nonnegative CHECK (tokens_used >= 0),
    CONSTRAINT gateway_daily_usage_cost_nonnegative CHECK (cost_used_usd >= 0)
);

CREATE INDEX IF NOT EXISTS idx_gateway_daily_usage_date
    ON gateway_daily_usage (usage_date);
