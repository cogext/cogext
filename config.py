from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    DATABASE_URL: str
    # DeepSeek is the active provider. Groq stays declared as a dead fallback:
    # it is only reached if LLM_PROVIDER is set back to "groq" explicitly.
    LLM_PROVIDER: str = "deepseek"
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
    # Private key for verifying observer-signed live receipts. No default: the
    # old default was public, so receipts signed with it are forgeable. Unset,
    # short or publicly known keys make every receipt "unverifiable".
    OBSERVE_RECEIPT_KEY: str = ""
    # ISO-8601 timestamp with timezone. Receipts stored before it whose
    # signature matches the old public observer key are reported "legacy"
    # (never "valid"). Unset: none are. Never set it in the future.
    LIVE_RECEIPT_LEGACY_CUTOFF: str = ""
    # Present in .env and read via os.getenv in app/api/paypal_webhook.py.
    # Declared here because pydantic-settings rejects undeclared env keys.
    RESEND_API_KEY: str = ""
    PAYPAL_WEBHOOK_ID: str = ""
    # REST app credentials, used only to obtain the OAuth token that PayPal's
    # verify-webhook-signature API requires. Read via os.getenv in paypal_webhook.py.
    PAYPAL_CLIENT_ID: str = ""
    PAYPAL_CLIENT_SECRET: str = ""
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    NOTIFY_EMAIL: str = "hello@cogextai.com"
    # Sender for notification mail. Must be on a domain verified in Resend.
    NOTIFY_FROM: str = "hello@cogextai.com"


settings = Settings()
