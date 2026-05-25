terraform {
  required_version = ">= 1.5.0"
}

resource "terraform_data" "exemplo" {
  input = {
    nome = "laboratorio"
