resource "aws_eks_addon" "vpc_cni" {
  cluster_name = aws_eks_cluster.gateway.name
  addon_name   = "vpc-cni"

  configuration_values = jsonencode({
    enableNetworkPolicy = "true"
  })

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  depends_on = [aws_eks_node_group.gateway]
}
