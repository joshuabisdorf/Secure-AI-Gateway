resource "aws_vpc" "gateway" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = local.name
  }
}

resource "aws_internet_gateway" "gateway" {
  vpc_id = aws_vpc.gateway.id

  tags = {
    Name = "${local.name}-igw"
  }
}

resource "aws_subnet" "public" {
  for_each = local.public_subnets

  vpc_id                  = aws_vpc.gateway.id
  availability_zone       = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = false

  tags = {
    Name                                  = "${local.name}-public-${each.key}"
    "kubernetes.io/role/elb"              = "1"
    "kubernetes.io/cluster/${local.name}" = "shared"
  }
}

resource "aws_subnet" "private" {
  for_each = local.private_subnets

  vpc_id            = aws_vpc.gateway.id
  availability_zone = each.key
  cidr_block        = each.value

  tags = {
    Name                                  = "${local.name}-private-${each.key}"
    "kubernetes.io/role/internal-elb"     = "1"
    "kubernetes.io/cluster/${local.name}" = "shared"
  }
}

resource "aws_subnet" "data" {
  for_each = local.data_subnets

  vpc_id            = aws_vpc.gateway.id
  availability_zone = each.key
  cidr_block        = each.value

  tags = {
    Name = "${local.name}-data-${each.key}"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.gateway.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.gateway.id
  }

  tags = {
    Name = "${local.name}-public"
  }
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_eip" "nat" {
  for_each = var.nat_gateway_mode == "per_az" ? aws_subnet.public : { primary = aws_subnet.public[local.azs[0]] }

  domain = "vpc"

  tags = {
    Name = "${local.name}-nat-${each.key}"
  }
}

resource "aws_nat_gateway" "gateway" {
  for_each = aws_eip.nat

  allocation_id = each.value.id
  subnet_id     = var.nat_gateway_mode == "per_az" ? aws_subnet.public[each.key].id : aws_subnet.public[local.azs[0]].id

  tags = {
    Name = "${local.name}-nat-${each.key}"
  }

  depends_on = [aws_internet_gateway.gateway]
}

resource "aws_route_table" "private" {
  for_each = aws_subnet.private

  vpc_id = aws_vpc.gateway.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = var.nat_gateway_mode == "per_az" ? aws_nat_gateway.gateway[each.key].id : aws_nat_gateway.gateway["primary"].id
  }

  tags = {
    Name = "${local.name}-private-${each.key}"
  }
}

resource "aws_route_table_association" "private" {
  for_each = aws_subnet.private

  subnet_id      = each.value.id
  route_table_id = aws_route_table.private[each.key].id
}

resource "aws_route_table" "data" {
  for_each = aws_subnet.data

  vpc_id = aws_vpc.gateway.id

  tags = {
    Name = "${local.name}-data-${each.key}"
  }
}

resource "aws_route_table_association" "data" {
  for_each = aws_subnet.data

  subnet_id      = each.value.id
  route_table_id = aws_route_table.data[each.key].id
}
