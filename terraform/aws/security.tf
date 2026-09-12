resource "aws_kms_key" "platform" {
  description             = "${local.name} platform data encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = {
    Name = "${local.name}-platform"
  }
}

resource "aws_kms_alias" "platform" {
  name          = "alias/${local.name}-platform"
  target_key_id = aws_kms_key.platform.key_id
}

resource "aws_secretsmanager_secret" "provider_credentials" {
  name                    = "${local.name}/provider-credentials"
  description             = "Provider credentials populated during the cloud deployment stage."
  kms_key_id              = aws_kms_key.platform.arn
  recovery_window_in_days = var.protect_data ? 30 : 7
}

resource "aws_secretsmanager_secret" "database_credentials" {
  name                    = "${local.name}/database-credentials"
  description             = "Gateway database credentials populated during the cloud deployment stage."
  kms_key_id              = aws_kms_key.platform.arn
  recovery_window_in_days = var.protect_data ? 30 : 7
}

resource "aws_secretsmanager_secret" "tool_signing_key" {
  name                    = "${local.name}/tool-execution-signing-key"
  description             = "Gateway tool execution signing key populated during the cloud deployment stage."
  kms_key_id              = aws_kms_key.platform.arn
  recovery_window_in_days = var.protect_data ? 30 : 7
}
