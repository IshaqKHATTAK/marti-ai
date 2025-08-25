from functools import lru_cache
import json
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from urllib.parse import quote_plus
from dotenv import find_dotenv, load_dotenv
from typing import Optional
from typing import Annotated

load_dotenv(override=True)

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict

def parse_cors(v) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",")]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)

class Settings(BaseSettings):
    APP_NAME: str
    FRONTEND_HOST: str

    # Database settings
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_HOST: str
    POSTGRES_PORT: str
    DATABASE_URI_ASYNC: str

    # Make Redis settings optional with default values
    REDIS_HOST: str
    REDIS_PORT: str

    # JWT settings
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    REFRESH_TOKEN_EXPIRE_DAYS: int 
    ALGORITHM: str = "HS256"

    # OpenAI settings
    OPENAI_API_KEY: str

    # Pinecone settings
    PINECONE_API_KEY: str
    PINECONE_ENV: str
    PINECONE_KNOWLEDGE_BASE_INDEX: str
    EMBEDDINGS_MODEL: str
    # Optional settings
    ENVIRONMENT: Optional[str] = None
    DEBUG: bool
    
    # AWS settings (if needed)
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_REGION: Optional[str] = None
    ECR_REPOSITORY: str
    REDIS_HOST: str
    REDIS_PORT: str
    
    #Redis settings only for development server.
    TIME_TO_LIVE_IN_SECONDS:str
    PUBLIC_TIME_TO_LIVE_IN_SECONDS: str
    MAX_SESSIONS:int = 10000

    #Rate limiting
    USER_REQUESTS_PER_X_SECONDS: int
    USER_TOKENS_PER_X_SECONDS: int
    USER_UPLOADS_PER_X_SECONDS: int
    

    APP_REQUESTS_PER_X_SECONDS: int
    APP_TOKENS_PER_X_SECONDS: int
    APP_UPLOADS_PER_X_SECONDS: int

    FINGERPRINT_DURATION_SECONDS: int
    USER_KEY_DURATION_SECONDS: int
    USER_FILEUPLOADS_KEY_DURATION_SECONDS: int

    APP_KEY_DURATION_SECONDS: int
    APP_FILEUPLOADS_KEY_DURATION_SECONDS: int

    # Email Settings
    MAIL_USERNAME: str
    MAIL_PASSWORD: str
    MAIL_FROM: str
    MAIL_PORT: int 
    MAIL_SERVER: str 
    MAIL_STARTTLS: bool
    MAIL_SSL_TLS: bool
    MAIL_USE_CREDENTIALS: bool
    VERIFICATION_TOKEN_SECRET: str
    MAIL_FROM_NAME: str
    MAIL_DEBUG: bool

    BUCKET_NAME: str
    SQS_QUEUE_URL: str
    DELETE_SQS_URL: str
    GENERATE_IMAGE_SQS_URL:str

    FREE_TRAIL_DAYS: int

    STRIPE_PUBLIC_KEY: str
    STRIPE_SECRET_KEY: str
    STRIPE_WEBHOOK_SECRET: str

    TEIR2_CHATBOTS: int
    TEIR3_CHATBOTS: int
    CHATBOT_SECRET_KEY: str
    
    TOTAL_NO_OF_ALLOWED_DOCS: int
    TOTAL_NO_OF_ALLOWED_URLS: int
    PER_URL_CHAR_CONTENT: int
    TOTAL_NO_OF_QAS: int
    PER_QA_CHAR_LEN: int
    TOTAL_NO_OF_GUARDRAILS: int
    PER_GUARDRAILS_CHAR_LEN: int
    TRANING_TEXT_CHAR_LENGTH: int
    PER_DAYS_MESSAGES_FOR_FREMIUM: int
    PER_DAYS_MESSAGES_FOR_FREE_TIER: int
    PER_DAYS_MESSAGES_FOR_SUBSCRIBED: int
    FREE_TRAIL_DAYS: int
    TOTAL_ALLOWED_USERS_FOR_PAID: int
    TOTAL_NO_OF_ALLOWED_BULK_USERS: int
    
    TOTAL_NO_OF_MEMORY: int
    PER_MEMORY_CHAR_LEN: int
    TOTAL_NO_OF_FEEDBACK_EACH_MESSAGE: int
    PER_FEEDBACK_CHAR_LEN: int
    COOKIE_DOMAIN: str
    BACKEND_CORS_ORIGINS: Annotated[list[str] | str, BeforeValidator(parse_cors)] = []
    FREE_TIER_CHATBOTS: int
    FREE_TIER_USERS: int
    ADMIN_MESSAGES_WITH_EXTERNAL_PER_DAY_FREMIUM: int # This is total messages per month
    PUBLIC_MESSAGES_WITH_EXTERNAL_PER_DAY_FREMIUM: int
    STRIPE_IMAGE_GENERATION_PRICE_ID: str
    COMPLETE_PAY_STRIPE_COUPON_ID: str
    COMPLETE_PAY_COUPON_CODE: str
    STRIPE_STARTER_PRICE_ID: str
    STRIPE_ENTERPRISE_PRICE_ID_MONTHLY: str
    STRIPE_ENTERPRISE_PRICE_ID_YEARLY: str

    LANGSMITH_TRACING: bool
    LANGSMITH_ENDPOINT: str
    LANGSMITH_API_KEY: str
    LANGSMITH_PROJECT: str
    class Config:
        env_file = ".env"
        case_sensitive = True

def get_envs_setting():
    return Settings()

