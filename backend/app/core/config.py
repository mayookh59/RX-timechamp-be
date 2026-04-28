"""Application configuration using pydantic-settings.

Loads configuration from environment variables and .env file.
All settings are validated at startup to fail fast on misconfiguration.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Attributes:
        DATABASE_URL: PostgreSQL connection string using asyncpg driver.
        REDIS_URL: Redis connection string for caching and Celery broker.
        SECRET_KEY: Secret key for JWT token signing.
        ALGORITHM: JWT signing algorithm.
        ACCESS_TOKEN_EXPIRE_MINUTES: Access token TTL in minutes.
        REFRESH_TOKEN_EXPIRE_DAYS: Refresh token TTL in days.
        S3_BUCKET: S3 bucket name for screenshot storage.
        S3_ENDPOINT: S3-compatible endpoint URL.
        AWS_ACCESS_KEY_ID: AWS access key for S3.
        AWS_SECRET_ACCESS_KEY: AWS secret key for S3.
        VERSION: Application version string.
        CORS_ORIGINS: Comma-separated list of allowed CORS origins.
        LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        DB_POOL_SIZE: SQLAlchemy connection pool size.
        DB_MAX_OVERFLOW: SQLAlchemy max overflow connections.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        env_ignore_empty=True,
    )

    # Database — use os.environ fallback for Railway/cloud deployments
    DATABASE_URL: str = __import__("os").environ.get("DATABASE_URL", "postgresql+asyncpg://trackme:trackme@localhost:5432/trackme")
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 80

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT Authentication
    SECRET_KEY: str = "change-me-in-production-use-openssl-rand-hex-32"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # S3 Storage
    S3_BUCKET: str = "trackme-screenshots"
    S3_ENDPOINT: str = "http://localhost:9000"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""

    # Application
    VERSION: str = "1.0.0"
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"
    LOG_LEVEL: str = "INFO"

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS_ORIGINS string into a list of origins.

        Returns:
            List of allowed origin strings.
        """
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def async_database_url(self) -> str:
        """Convert DATABASE_URL to async driver format.

        Railway and other PaaS providers set DATABASE_URL with postgresql://
        but SQLAlchemy async requires postgresql+asyncpg://.
        Also strips sslmode param which asyncpg doesn't support.

        Returns:
            Database URL with the correct async driver prefix.
        """
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        # asyncpg doesn't support sslmode parameter — strip it
        url = url.replace("?sslmode=disable", "").replace("&sslmode=disable", "")
        url = url.replace("?sslmode=require", "").replace("&sslmode=require", "")
        return url


settings = Settings()
