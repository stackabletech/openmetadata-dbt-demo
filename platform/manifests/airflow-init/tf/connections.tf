1terraform {
  required_providers {
    airflow = {
      source  = "drfaust92/airflow"
      version = "1.0.2"
    }
  }
}

provider "airflow" {
  base_endpoint            = var.airflow_base_url
  oauth2_token             = var.airflow_token
  disable_ssl_verification = true
  # Airflow 3.x uses /api/v2
  base_path = "/api/v2"
}

variable "airflow_base_url" {
  default = "http://airflow-webserver-default-headless:8080"
}

variable "airflow_token" {
  description = "JWT token obtained from Airflow /auth/token endpoint"
  sensitive   = true
}

resource "airflow_connection" "trino_default" {
  connection_id = "trino_default"
  conn_type     = "trino"
  host          = "trino-coordinator"
  port          = 8443
  login         = "admin"
  schema        = "hive-iceberg"
  extra = jsonencode({
    protocol = "https"
    verify   = false
  })
}
