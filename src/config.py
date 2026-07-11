from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Payment gateway
    gateway_provider: str = "stripe"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    mercado_pago_access_token: str = ""

    # LLM — provider-agnostic
    llm_provider: str = "anthropic"   # anthropic | openai
    llm_model: str = "claude-sonnet-4-6"
    llm_api_key: str = ""


settings = Settings()
