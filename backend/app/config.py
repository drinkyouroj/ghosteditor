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

    # LLM API
    llm_backend: str = "anthropic"  # "anthropic", "openai", or "groq"
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""  # Leave empty for Anthropic default
    openai_api_key: str = ""  # Also used for local models (can be "none" for local)
    openai_base_url: str = ""  # e.g. http://localhost:11434/v1 for Ollama
    groq_api_key: str = ""
    llm_model_bible: str = "claude-haiku-4-5-20251001"
    llm_model_analysis: str = "claude-haiku-4-5-20251001"
    llm_model_splitting: str = ""  # Defaults to llm_model_analysis if empty

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

    # App
    base_url: str = "http://localhost:5173"  # Frontend URL for emails and Stripe redirects

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
