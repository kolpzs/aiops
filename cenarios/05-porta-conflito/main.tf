# Cenário 05: Conflito de porta no container
# Dois containers tentam usar a mesma porta externa.

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

resource "docker_container" "app_a" {
  name  = "tcc-mvp-05-app-a"
  image = docker_image.app.image_id

  env = [
    "DATABASE_HOST=db.local",
    "APP_PORT=5000",
  ]

  ports {
    internal = 5000
    external = 5055
  }

  must_run = true
}

resource "docker_container" "app_b" {
  name  = "tcc-mvp-05-app-b"
  image = docker_image.app.image_id

  env = [
    "DATABASE_HOST=db.local",
    "APP_PORT=5000",
  ]

  # ERRO PROPOSITAL: mesma porta externa que app_a
  ports {
    internal = 5000
    external = 5055
  }

  must_run   = true
  depends_on = [docker_container.app_a]
}
