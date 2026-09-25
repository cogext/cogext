from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    DATABASE_URL: str
    LLM_PROVIDER: str = "groq"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama3-70b-8192"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-chat"
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    APP_ENV: str = "development"
    SLACK_WEBHOOK_URL: str = ""
    ACTION_TOKEN_SECRET: str = ""
    # Present in .env and read via os.getenv in app/api/paypal_webhook.py.
    # Declared here because pydantic-settings rejects undeclared env keys.
    RESEND_API_KEY: str = ""
    PAYPAL_WEBHOOK_ID: str = ""
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    NOTIFY_EMAIL: str = "hello@cogextai.com"
    # Sender for notification mail. Must be on a domain verified in Resend.
    NOTIFY_FROM: str = "hello@cogextai.com"


settings = Settings()
