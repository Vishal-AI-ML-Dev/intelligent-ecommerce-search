# Regional WAF on the ALB. Free-plan projects cannot attach WAF to CloudFront, so rate limits key
# on the client address CloudFront forwards in X-Forwarded-For. That header's first address can
# be set by a client, so a fixed total limit on /search backs it up.
resource "aws_wafv2_web_acl" "main" {
  name  = var.name
  scope = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "per-client-rate"
    priority = 1

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit                 = var.waf_per_ip_limit
        evaluation_window_sec = 300
        aggregate_key_type    = "FORWARDED_IP"

        forwarded_ip_config {
          header_name       = "X-Forwarded-For"
          fallback_behavior = "MATCH"
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-per-client-rate"
      sampled_requests_enabled   = false
    }
  }

  rule {
    name     = "search-total-rate"
    priority = 2

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit                 = var.waf_search_total_limit
        evaluation_window_sec = 300
        aggregate_key_type    = "CONSTANT"

        scope_down_statement {
          byte_match_statement {
            search_string         = "/search"
            positional_constraint = "STARTS_WITH"

            field_to_match {
              uri_path {}
            }

            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-search-total-rate"
      sampled_requests_enabled   = false
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = var.name
    sampled_requests_enabled   = false
  }
}

resource "aws_wafv2_web_acl_association" "alb" {
  resource_arn = aws_lb.main.arn
  web_acl_arn  = aws_wafv2_web_acl.main.arn
}
