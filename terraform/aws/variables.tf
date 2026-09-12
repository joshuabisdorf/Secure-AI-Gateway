variable "aws_region" {
  description = "AWS Region for the Secure AI Gateway environment."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]+$", var.aws_region))
    error_message = "aws_region must look like a valid AWS Region name."
  }
}

variable "project_name" {
  description = "Short project identifier used in resource names and tags."
  type        = string
  default     = "secure-ai-gateway"

  validation {
    condition     = length(var.project_name) >= 3 && length(var.project_name) <= 24 && can(regex("^[a-z0-9-]+$", var.project_name))
    error_message = "project_name must be 3-24 lowercase alphanumeric/hyphen characters."
  }
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "dev"

  validation {
    condition     = length(var.environment) >= 2 && length(var.environment) <= 8 && can(regex("^[a-z0-9-]+$", var.environment))
    error_message = "environment must be 2-8 lowercase alphanumeric/hyphen characters."
  }
}

variable "vpc_cidr" {
  description = "IPv4 CIDR for the environment VPC."
  type        = string
  default     = "10.42.0.0/16"

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "vpc_cidr must be a valid IPv4 CIDR."
  }
}

variable "availability_zone_count" {
  description = "Number of Availability Zones used by the environment."
  type        = number
  default     = 3

  validation {
    condition     = var.availability_zone_count >= 2 && var.availability_zone_count <= 3
    error_message = "availability_zone_count must be 2 or 3."
  }
}

variable "nat_gateway_mode" {
  description = "NAT topology: single is cost-oriented; per_az provides zonal egress redundancy."
  type        = string
  default     = "single"

  validation {
    condition     = contains(["single", "per_az"], var.nat_gateway_mode)
    error_message = "nat_gateway_mode must be single or per_az."
  }
}

variable "eks_version" {
  description = "Amazon EKS Kubernetes minor version."
  type        = string
  default     = "1.36"

  validation {
    condition     = can(regex("^1\\.[0-9]+$", var.eks_version))
    error_message = "eks_version must be a Kubernetes minor version such as 1.36."
  }
}

variable "eks_public_access_cidrs" {
  description = "CIDRs allowed to reach the public EKS API endpoint. Empty keeps the API private-only."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for cidr in var.eks_public_access_cidrs : can(cidrnetmask(cidr))])
    error_message = "Every eks_public_access_cidrs entry must be a valid IPv4 CIDR."
  }
}

variable "eks_admin_role_arn" {
  description = "Optional IAM role ARN granted EKS cluster-admin access through an EKS access entry."
  type        = string
  default     = null
  nullable    = true
}

variable "eks_node_instance_types" {
  description = "EC2 instance types for the managed EKS node group."
  type        = list(string)
  default     = ["t3.medium"]
}

variable "eks_node_min_size" {
  description = "Minimum managed node group size."
  type        = number
  default     = 2
}

variable "eks_node_desired_size" {
  description = "Desired managed node group size."
  type        = number
  default     = 2
}

variable "eks_node_max_size" {
  description = "Maximum managed node group size."
  type        = number
  default     = 4
}

variable "postgres_engine_version" {
  description = "RDS PostgreSQL engine version."
  type        = string
  default     = "18.6"
}

variable "postgres_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "postgres_allocated_storage_gib" {
  description = "Initial encrypted RDS storage in GiB."
  type        = number
  default     = 20
}

variable "postgres_max_allocated_storage_gib" {
  description = "Maximum RDS autoscaled storage in GiB."
  type        = number
  default     = 100
}

variable "postgres_multi_az" {
  description = "Whether RDS uses Multi-AZ deployment."
  type        = bool
  default     = false
}

variable "valkey_engine_version" {
  description = "ElastiCache Valkey engine version."
  type        = string
  default     = "8.2"
}

variable "valkey_node_type" {
  description = "ElastiCache node type."
  type        = string
  default     = "cache.t4g.micro"
}

variable "protect_data" {
  description = "Enable deletion protection/final snapshots for persistent data services. Recommended outside disposable development environments."
  type        = bool
  default     = false
}

variable "ecr_force_delete" {
  description = "Allow Terraform to delete a non-empty ECR repository. Keep false outside disposable development environments."
  type        = bool
  default     = false
}
