# Cenário 06: Dependência Python quebrada no build da imagem
# O Dockerfile tenta instalar um pacote inexistente, fazendo o build falhar.

terraform {
  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "~> 3.0"
    }
  }
}

provider "docker" {}

resource "docker_image" "app_broken" {
  name = "tcc-mvp-app-broken:latest"
  build {
    context    = "${path.module}/app-src"
    dockerfile = "Dockerfile"
  }
}

resource "docker_container" "app" {
  name  = "tcc-mvp-06-dep-quebrada"
  image = docker_image.app_broken.image_id

  env = [
    "DATABASE_HOST=db.local",
  ]

  ports {
    internal = 5000
    external = 5006
  }

  must_run = true
}
