terraform {
  required_version = ">= 1.5.0"
}

resource "terraform_data" "servico_a" {
  input = terraform_data.servico_b.output
}

resource "terraform_data" "servico_b" {
  input = terraform_data.servico_a.output
}
