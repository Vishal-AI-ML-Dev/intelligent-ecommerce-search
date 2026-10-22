# The app role's password is ephemeral and write-only: it is generated at apply time, written to
# Secrets Manager and never stored in Terraform state or plan files.
ephemeral "random_password" "app_db" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "app_db" {
  name                    = "${var.name}/app-db"
  description             = "Password of the SELECT-only PostgreSQL role used by the Search API"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app_db" {
  secret_id                = aws_secretsmanager_secret.app_db.id
  secret_string_wo         = jsonencode({ password = ephemeral.random_password.app_db.result })
  secret_string_wo_version = 1
}

# Shared value that CloudFront sends and the ALB requires (origin verification, not a data
# credential). It is stored in the encrypted, private state bucket and is never an output.
resource "random_password" "origin_verify" {
  length  = 48
  special = false
}
