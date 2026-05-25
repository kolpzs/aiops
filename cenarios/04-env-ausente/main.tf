# Cenário 04: Container sem variável de ambiente obrigatória
# A app Flask exige DATABASE_HOST. Sem ela, o container crasheia no startup.

terraform {
  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "~> 3.0"
    }
  }
}

provider "docker" {}

resource "docker_image" "app" {
  name = "tcc-mvp-app:latest"
  build {
    context    = "${path.module}/../../app-demo"
    dockerfile = "Dockerfile"
  }
}

resource "docker_container" "app" {
  name  = "tcc-mvp-04-env-ausente"
  image = docker_image.app.image_id

  # ERRO PROPOSITAL: DATABASE_HOST não está definido
  # A aplicação Flask faz sys.exit(1) se a variável não existir
  env = [
    "APP_PORT=5000",
  ]

  ports {
    internal = 5000
    external = 5004
  }

  # must_run = false: Terraform não falha se o container parar
  # O erro de aplicação será detectado via logs do container (exit code 1)
  must_run = false
}
