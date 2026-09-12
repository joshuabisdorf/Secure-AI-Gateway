output "state_bucket_name" {
  description = "S3 bucket used for Terraform state."
  value       = aws_s3_bucket.terraform_state.bucket
}

output "state_kms_key_arn" {
  description = "KMS key ARN used to encrypt Terraform state."
  value       = aws_kms_key.terraform_state.arn
}

output "backend_init_example" {
  description = "Example partial-backend initialization command for terraform/aws."
  value = join(" ", [
    "terraform init -reconfigure",
    "-backend-config=\"bucket=${aws_s3_bucket.terraform_state.bucket}\"",
    "-backend-config=\"region=${var.aws_region}\"",
    "-backend-config=\"key=secure-ai-gateway/dev/terraform.tfstate\"",
    "-backend-config=\"use_lockfile=true\"",
    "-backend-config=\"encrypt=true\"",
    "-backend-config=\"kms_key_id=${aws_kms_key.terraform_state.arn}\"",
  ])
}
