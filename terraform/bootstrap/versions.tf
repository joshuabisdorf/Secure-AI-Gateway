terraform {
  required_version = ">= 1.16.2, < 1.17.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.62.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "secure-ai-gateway"
      ManagedBy = "terraform"
      Purpose   = "terraform-state"
    }
  }
}
