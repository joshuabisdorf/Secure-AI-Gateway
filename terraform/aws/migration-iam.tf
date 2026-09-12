data "aws_iam_policy_document" "migration_workload_assume" {
  statement {
    actions = [
      "sts:AssumeRole",
      "sts:TagSession",
    ]

    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "migration_workload" {
  name               = "${local.name}-migration-workload"
  assume_role_policy = data.aws_iam_policy_document.migration_workload_assume.json
}

data "aws_iam_policy_document" "migration_workload" {
  statement {
    sid = "ReadDatabaseBootstrapSecrets"
    actions = [
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetSecretValue",
    ]
    resources = [
      aws_secretsmanager_secret.database_credentials.arn,
      aws_db_instance.gateway.master_user_secret[0].secret_arn,
    ]
  }

  statement {
    sid       = "DecryptDatabaseBootstrapSecrets"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.platform.arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "migration_workload" {
  name   = "database-bootstrap"
  role   = aws_iam_role.migration_workload.id
  policy = data.aws_iam_policy_document.migration_workload.json
}

resource "aws_eks_pod_identity_association" "migration" {
  cluster_name    = aws_eks_cluster.gateway.name
  namespace       = "secure-ai-gateway"
  service_account = "sag-migration"
  role_arn        = aws_iam_role.migration_workload.arn

  depends_on = [aws_eks_addon.pod_identity_agent]
}
