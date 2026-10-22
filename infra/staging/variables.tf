variable "region" {
  description = "The project's assigned Region; Free-plan projects cannot create Regional resources elsewhere."
  type        = string
  default     = "ap-southeast-2"
}

variable "name" {
  description = "Prefix for every resource name."
  type        = string
  default     = "m26a-search"
}

variable "image_tag" {
  description = "Immutable ECR tag of the image to run: the full git commit SHA it was built from."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.image_tag))
    error_message = "image_tag must be a full 40-character git commit SHA."
  }
}

variable "alert_email" {
  description = "Recipient of the budget alert. Supplied at plan time (terraform.tfvars is git-ignored)."
  type        = string
}

variable "budget_alert_usd" {
  description = "Monthly cost (before credits) above which the alert email is sent. An alert, not a cap."
  type        = number
  default     = 10
}

variable "app_cpu" {
  description = "Fargate task CPU units. 512 = 0.5 vCPU."
  type        = number
  default     = 512
}

variable "app_memory" {
  description = "Fargate task memory (MiB). Measured local peak was about 499 MiB (docs/m26a-progress.md)."
  type        = number
  default     = 2048
}

variable "db_instance_class" {
  description = "Free-plan RDS classes are db.t3.micro and db.t4g.micro only."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_engine_version" {
  description = "PostgreSQL 16 minor version offered in the Region (local development uses pg16)."
  type        = string
  default     = "16.15"
}

variable "waf_per_ip_limit" {
  description = "Requests per client IP (X-Forwarded-For) per 5 minutes before WAF blocks."
  type        = number
  default     = 300
}

variable "waf_search_total_limit" {
  description = "Backstop: total /search requests per 5 minutes across all clients before WAF blocks."
  type        = number
  default     = 3000
}

variable "app_desired_count" {
  description = "Running API tasks. The first apply uses 0, so no task starts before the setup task has prepared the database; then 1."
  type        = number
  default     = 1

  validation {
    condition     = contains([0, 1], var.app_desired_count)
    error_message = "app_desired_count must be 0 (stopped) or 1."
  }
}
