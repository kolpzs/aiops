terraform {
  required_version = ">= 1.5.0"
}

locals {
  service_name = "API_Principal"
}

resource "terraform_data" "servico" {
  input = local.service_name

  lifecycle {
    precondition {
      condition     = can(regex("^[a-z0-9-]+$", local.service_name))
      error_message = "service_name deve usar apenas letras minusculas, numeros e hifen."
    }
  }
}
