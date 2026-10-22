locals {
  image         = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
  app_db_user   = "search_app"
  master_secret = aws_db_instance.main.master_user_secret[0].secret_arn
  db_environment = [
    { name = "APP_ENV", value = "production" },
    { name = "LOG_LEVEL", value = "INFO" },
    { name = "POSTGRES_HOST", value = aws_db_instance.main.address },
    { name = "POSTGRES_PORT", value = tostring(aws_db_instance.main.port) },
    { name = "POSTGRES_DB", value = aws_db_instance.main.db_name },
  ]

  # One-off setup on the empty database, as the master user: the documented sequence
  # (docs/DEMO.md) on the synthetic seed baked into the image, the read-only checks, then the
  # SELECT-only API role. Run only by an explicit `aws ecs run-task` (docs/m26a-progress.md).
  # CRLF from a Windows checkout would break `sh`; the container always gets LF.
  setup_script = replace(file("${path.module}/setup.sh"), "\r\n", "\n")
}

resource "aws_ecs_cluster" "main" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/${var.name}/app"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "setup" {
  name              = "/${var.name}/setup"
  retention_in_days = 14
}

resource "aws_ecs_task_definition" "app" {
  family                   = "${var.name}-app"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.app_cpu
  memory                   = var.app_memory
  execution_role_arn       = aws_iam_role.app_execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name         = "api"
    image        = local.image
    essential    = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment  = concat(local.db_environment, [{ name = "POSTGRES_USER", value = local.app_db_user }])
    secrets = [
      { name = "POSTGRES_PASSWORD", valueFrom = "${aws_secretsmanager_secret.app_db.arn}:password::" },
    ]
    healthCheck = {
      command     = ["CMD", "python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "api"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "setup" {
  family                   = "${var.name}-setup"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.app_cpu
  memory                   = var.app_memory
  execution_role_arn       = aws_iam_role.setup_execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name        = "setup"
    image       = local.image
    essential   = true
    command     = ["sh", "-c", local.setup_script]
    environment = concat(local.db_environment, [{ name = "APP_DB_USER", value = local.app_db_user }])
    secrets = [
      { name = "POSTGRES_USER", valueFrom = "${local.master_secret}:username::" },
      { name = "POSTGRES_PASSWORD", valueFrom = "${local.master_secret}:password::" },
      { name = "APP_DB_PASSWORD", valueFrom = "${aws_secretsmanager_secret.app_db.arn}:password::" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.setup.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "setup"
      }
    }
  }])
}

resource "aws_ecs_service" "app" {
  name             = "${var.name}-app"
  cluster          = aws_ecs_cluster.main.id
  task_definition  = aws_ecs_task_definition.app.arn
  desired_count    = var.app_desired_count
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  # One task; a deploy briefly runs two, and a failed deploy rolls back to the last good one.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 60

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener_rule.from_cloudfront]
}
