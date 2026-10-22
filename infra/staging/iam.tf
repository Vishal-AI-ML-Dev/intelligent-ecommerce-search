# Two task execution roles, so the API task can read only its own secret and the master secret
# is reachable only from the one-off setup task. Neither task gets a task role: the application
# calls no AWS API.
data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app_execution" {
  name               = "${var.name}-app-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role" "setup_execution" {
  name               = "${var.name}-setup-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "app_execution" {
  role       = aws_iam_role.app_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy_attachment" "setup_execution" {
  role       = aws_iam_role.setup_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "app_secrets" {
  name = "read-app-db-secret"
  role = aws_iam_role.app_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.app_db.arn]
    }]
  })
}

resource "aws_iam_role_policy" "setup_secrets" {
  name = "read-master-and-app-db-secrets"
  role = aws_iam_role.setup_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_db_instance.main.master_user_secret[0].secret_arn, aws_secretsmanager_secret.app_db.arn]
    }]
  })
}
