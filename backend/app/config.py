from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://ghosteditor:ghosteditor_dev@localhost:5432/ghosteditor"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # JWT
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    provisional_token_expire_minutes: int = 60

    # Claude API
    anthropic_api_key: str = ""

    # AWS S3 / MinIO
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    s3_bucket_name: str = "ghosteditor-manuscripts"
    aws_region: str = "us-east-1"
    s3_endpoint_url: str = ""  # Set to http://localhost:9000 for MinIO

    # Email
    resend_api_key: str = ""

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
