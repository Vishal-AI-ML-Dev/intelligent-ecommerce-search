# M26A Search staging demo (docs/m26a-progress.md). Credentials come only from the caller's
# AWS profile (`aws login`); nothing here reads or writes a credential into state or outputs.
terraform {
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.69.1"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.9.1"
    }
  }

  # Partial configuration: the bucket comes from `-backend-config=backend.hcl` (git-ignored),
  # created by ./bootstrap. Versioned, encrypted, public access blocked, S3-native locking.
  backend "s3" {
    key          = "m26a/staging.tfstate"
    region       = "ap-southeast-2"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "m26a-search-staging"
      ManagedBy = "terraform"
    }
  }
}
