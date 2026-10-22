# Single-AZ, private, encrypted PostgreSQL 16. RDS generates and stores the master password in
# Secrets Manager (manage_master_user_password), so it never passes through Terraform. The master
# user is used only by the one-off setup task; the API connects as the SELECT-only app role.
resource "aws_db_subnet_group" "main" {
  name       = var.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "main" {
  identifier     = var.name
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  allocated_storage     = 20
  max_allocated_storage = 0
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name                     = "ecommerce_search"
  username                    = "search_admin"
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = false

  # Synthetic, reproducible data: one day of automated backups is enough.
  backup_retention_period      = 1
  auto_minor_version_upgrade   = true
  performance_insights_enabled = false
  monitoring_interval          = 0
  copy_tags_to_snapshot        = true

  # Deleting the database needs an explicit, reviewed change: lift both guards, then destroy.
  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.name}-final"

  lifecycle {
    prevent_destroy = true
  }
}
