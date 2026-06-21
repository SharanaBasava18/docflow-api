import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


class Config:
    """Runtime configuration for DocFlow.

    Defaults make importing the application safe in local tooling; production
    must provide a strong JWT secret and real service credentials.
    """

    ENV = os.getenv("ENV", "development")
    APP_UPLOAD_DIR = os.getenv("APP_UPLOAD_DIR", str(Path("/tmp/docflow-uploads")))
    APP_MAX_CHUNK_SIZE = int(os.getenv("APP_MAX_CHUNK_SIZE", "10485760"))

    DATABASE_URL = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://docflow:docflow@db-postgres:5432/docflow"
    )
    TEST_DATABASE_URL = os.getenv(
        "TEST_DATABASE_URL", "postgresql+psycopg://docflow:docflow@db-postgres:5432/docflow_test"
    )

    @property
    def database_url(self) -> str:
        return self.TEST_DATABASE_URL if self.ENV == "testing" else self.DATABASE_URL

    # Compatibility aliases for modules that will be moved in later phases.
    @property
    def POSTGRES_DATABASE_URL(self) -> str:
        return self.database_url

    @property
    def CELERY_BACKEND_ENDPOINT(self) -> str:
        return self.database_url.replace("postgresql+psycopg://", "db+postgresql+psycopg://")

    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-this-development-secret")
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

    MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
    MINIO_URL = os.getenv("MINIO_URL", "http://localhost:9001")
    MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    MINIO_PUBLIC_BUCKET = os.getenv("MINIO_PUBLIC_BUCKET", "public")
    MINIO_PRIVATE_BUCKET = os.getenv("MINIO_PRIVATE_BUCKET", "private")

    RABBITMQ_DEFAULT_USER = os.getenv("RABBITMQ_DEFAULT_USER", "docflow")
    RABBITMQ_DEFAULT_PASS = os.getenv("RABBITMQ_DEFAULT_PASS", "docflow")
    RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
    RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))

    @property
    def RABBITMQ_ENDPOINT(self) -> str:
        return (
            f"pyamqp://{self.RABBITMQ_DEFAULT_USER}:{self.RABBITMQ_DEFAULT_PASS}"
            f"@{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}//"
        )


config = Config()
