# Identifiers only; no secret, password or origin-verification value is ever an output.
output "url" {
  description = "Public HTTPS URL of the Search demo"
  value       = "https://${aws_cloudfront_distribution.main.domain_name}/"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "cluster" {
  value = aws_ecs_cluster.main.name
}

output "service" {
  value = aws_ecs_service.app.name
}

output "setup_task_definition" {
  value = aws_ecs_task_definition.setup.arn
}

output "task_subnets" {
  value = aws_subnet.public[*].id
}

output "task_security_group" {
  value = aws_security_group.app.id
}
