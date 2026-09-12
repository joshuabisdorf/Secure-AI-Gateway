resource "aws_db_subnet_group" "gateway" {
  name       = "${local.name}-db"
  subnet_ids = values(aws_subnet.data)[*].id

  tags = {
    Name = "${local.name}-db"
  }
}

resource "aws_security_group" "postgres" {
  name        = "${local.name}-postgres"
  description = "PostgreSQL access from Secure AI Gateway EKS workloads"
  vpc_id      = aws_vpc.gateway.id

  ingress {
    description     = "PostgreSQL from EKS cluster security group"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_eks_cluster.gateway.vpc_config[0].cluster_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${local.name}-postgres"
  }
}

resource "aws_db_parameter_group" "gateway" {
  name   = "${local.name}-postgres18"
  family = "postgres18"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "gateway" {
  identifier = "${local.name}-postgres"

  engine         = "postgres"
  engine_version = var.postgres_engine_version
  instance_class = var.postgres_instance_class

  db_name  = "secure_ai_gateway"
  username = "sag_admin"
  port     = 5432

  manage_master_user_password   = true
  master_user_secret_kms_key_id = aws_kms_key.platform.arn

  allocated_storage     = var.postgres_allocated_storage_gib
  max_allocated_storage = var.postgres_max_allocated_storage_gib
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.platform.arn

  db_subnet_group_name   = aws_db_subnet_group.gateway.name
  vpc_security_group_ids = [aws_security_group.postgres.id]
  publicly_accessible    = false
  multi_az               = var.postgres_multi_az

  parameter_group_name = aws_db_parameter_group.gateway.name

  backup_retention_period = 7
  copy_tags_to_snapshot    = true
  auto_minor_version_upgrade = true
  iam_database_authentication_enabled = true

  deletion_protection       = var.protect_data
  delete_automated_backups  = !var.protect_data
  skip_final_snapshot       = !var.protect_data
  final_snapshot_identifier = var.protect_data ? "${local.name}-postgres-final" : null

  apply_immediately = false
}

resource "aws_elasticache_subnet_group" "gateway" {
  name       = "${local.name}-cache"
  subnet_ids = values(aws_subnet.data)[*].id
}

resource "aws_security_group" "valkey" {
  name        = "${local.name}-valkey"
  description = "Valkey access from Secure AI Gateway EKS workloads"
  vpc_id      = aws_vpc.gateway.id

  ingress {
    description     = "Valkey TLS from EKS cluster security group"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_eks_cluster.gateway.vpc_config[0].cluster_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${local.name}-valkey"
  }
}

resource "aws_elasticache_user" "gateway" {
  user_id       = "${local.name}-gateway"
  user_name     = "${local.name}-gateway"
  access_string = "on ~* +@all"
  engine        = "valkey"

  authentication_mode {
    type = "iam"
  }
}

resource "aws_elasticache_user_group" "gateway" {
  engine        = "valkey"
  user_group_id = "${local.name}-gateway"
  user_ids      = [aws_elasticache_user.gateway.user_id]
}

resource "aws_elasticache_replication_group" "gateway" {
  replication_group_id = "${local.name}-cache"
  description          = "Secure AI Gateway distributed rate-limit and replay state"

  engine         = "valkey"
  engine_version = var.valkey_engine_version
  node_type      = var.valkey_node_type
  port           = 6379

  num_cache_clusters         = 2
  automatic_failover_enabled = true
  multi_az_enabled           = true

  subnet_group_name  = aws_elasticache_subnet_group.gateway.name
  security_group_ids = [aws_security_group.valkey.id]
  user_group_ids     = [aws_elasticache_user_group.gateway.user_group_id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  kms_key_id                  = aws_kms_key.platform.arn

  snapshot_retention_limit = var.protect_data ? 7 : 1
  snapshot_window          = "03:00-04:00"
  maintenance_window       = "sun:04:00-sun:05:00"
  apply_immediately        = false
}
