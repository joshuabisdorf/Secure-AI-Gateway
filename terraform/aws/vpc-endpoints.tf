resource "aws_security_group" "secretsmanager_endpoint" {
  name_prefix = "${local.name}-secretsmanager-vpce-"
  description = join(" ", [
    "Allow Secure AI Gateway private subnets to reach Secrets Manager",
    "privately.",
  ])
  vpc_id      = aws_vpc.gateway.id

  ingress {
    description = "HTTPS from EKS private subnets"
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = values(aws_subnet.private)[*].cidr_block
  }

  tags = {
    Name = "${local.name}-secretsmanager-vpce"
  }
}

resource "aws_vpc_endpoint" "secretsmanager" {
  vpc_id              = aws_vpc.gateway.id
  service_name        = "com.amazonaws.${var.aws_region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = values(aws_subnet.private)[*].id
  security_group_ids  = [aws_security_group.secretsmanager_endpoint.id]

  tags = {
    Name = "${local.name}-secretsmanager"
  }
}
