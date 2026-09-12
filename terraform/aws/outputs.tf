output "vpc_id" {
  description = "VPC ID for the environment."
  value       = aws_vpc.gateway.id
}

output "public_subnet_ids" {
  description = "Public subnet IDs reserved for internet-facing load balancers."
  value       = values(aws_subnet.public)[*].id
}

output "private_subnet_ids" {
  description = "Private subnet IDs used by EKS worker nodes."
  value       = values(aws_subnet.private)[*].id
}

output "data_subnet_ids" {
  description = "Isolated subnet IDs used by RDS and ElastiCache."
  value       = values(aws_subnet.data)[*].id
}

output "eks_cluster_name" {
  description = "EKS cluster name."
  value       = aws_eks_cluster.gateway.name
}

output "eks_cluster_endpoint" {
  description = "EKS Kubernetes API endpoint."
  value       = aws_eks_cluster.gateway.endpoint
}

output "ecr_repository_url" {
  description = "ECR repository URL for Secure AI Gateway images."
  value       = aws_ecr_repository.gateway.repository_url
}

output "postgres_endpoint" {
  description = "Private RDS PostgreSQL endpoint."
  value       = aws_db_instance.gateway.address
}

output "postgres_port" {
  description = "RDS PostgreSQL port."
  value       = aws_db_instance.gateway.port
}

output "postgres_master_secret_arn" {
  description = "RDS-managed administrative secret ARN. This is not intended for gateway runtime use."
  value       = try(aws_db_instance.gateway.master_user_secret[0].secret_arn, null)
}

output "valkey_primary_endpoint" {
  description = "Private TLS-enabled Valkey primary endpoint."
  value       = aws_elasticache_replication_group.gateway.primary_endpoint_address
}

output "valkey_port" {
  description = "Valkey port."
  value       = aws_elasticache_replication_group.gateway.port
}

output "valkey_iam_user_id" {
  description = "ElastiCache IAM-authenticated user ID expected by the gateway cloud runtime."
  value       = aws_elasticache_user.gateway.user_id
}

output "gateway_workload_role_arn" {
  description = "IAM role associated with the sag-gateway Kubernetes service account through EKS Pod Identity."
  value       = aws_iam_role.gateway_workload.arn
}

output "runtime_secret_arns" {
  description = "Secret containers populated during the cloud deployment stage."
  value = {
    provider_credentials = aws_secretsmanager_secret.provider_credentials.arn
    database_credentials = aws_secretsmanager_secret.database_credentials.arn
    tool_signing_key      = aws_secretsmanager_secret.tool_signing_key.arn
  }
}
