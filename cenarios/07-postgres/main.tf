# Cenário 07: PostgreSQL sem variável POSTGRES_PASSWORD
# ERRO INTENCIONAL: O container PostgreSQL exige POSTGRES_PASSWORD ou
# POSTGRES_HOST_AUTH_METHOD=trust. Sem isso, o entrypoint encerra com erro fatal.
# A IA deve identificar a variável de ambiente ausente e sugerir a correção.

terraform {
  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "~> 3.0"
    }
  }
}

provider "docker" {}

resource "docker_image" "postgres" {
  name         = "postgres:15-alpine"
  keep_locally = true
}

resource "docker_container" "postgres_db" {
  name  = "tcc-mvp-postgres"
  image = docker_image.postgres.image_id

  # ERRO INTENCIONAL: POSTGRES_PASSWORD ausente.
  # O container vai subir, mas encerrará imediatamente com:
  # "Error: Database is uninitialized and superuser password is not specified."
  env = [
    "POSTGRES_DB=tcc_resultados",
    "POSTGRES_USER=tcc_user",
    # POSTGRES_PASSWORD propositalmente omitido
  ]

  ports {
    internal = 5432
    external = 5433
  }

  restart = "no"
}
