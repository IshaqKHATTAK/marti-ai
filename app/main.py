from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from app.routes import landing,user, organization, chatbot_config, user_chat, user_document, payment, stripe
from app.routes import notifications, events
from app.common.database_config import get_db
from app.services.super_admin import create_super_admin
from sqlalchemy.orm import Session
from app.common.env_config import get_envs_setting
from app.common.logging_config import setup_logging, get_logger
from app.common.middleware import LoggingMiddleware, CORSLoggingMiddleware
from app.common.selective_cors import SelectiveCORSMiddleware

# Initialize environment settings
envs = get_envs_setting()

# Setup logging
setup_logging()
logger = get_logger("main")

def create_application():
    application = FastAPI(
        title="Marti AI Backend",
        description="Backend API for Marti AI platform supporting multiple frontends",
        version="1.0.0"
    )
    
    # Add debug route
    @application.get("/api/v1/debug")
    async def debug():
        logger.info("Debug endpoint accessed")
        return {"routes": [{"path": route.path, "name": route.name} for route in application.routes]}
    
    # Include routers
    application.include_router(user.router)
    application.include_router(user.public_router)
    application.include_router(
        organization.router, 
        prefix="/api/v1/organizations",  # This was causing /organizations/organizations/
        tags=["organizations"]
    )
    application.include_router(chatbot_config.chatbot_config_routes)
    application.include_router(user_chat.chats_routes)
    application.include_router(user_chat.feedback_router)
    application.include_router(user_document.document_upload_routes)
    application.include_router(payment.payments_router_protected)
    application.include_router(stripe.stripe_router) 
    application.include_router(notifications.announcement_router) 
    application.include_router(landing.landing_router) 
    application.include_router(landing.faqs_router) 
    application.include_router(events.events_router) 
    
    return application

app = create_application()

# Configure CORS origins
if isinstance(envs.BACKEND_CORS_ORIGINS, list):
    cors_origins = envs.BACKEND_CORS_ORIGINS
else:
    cors_origins = [origins.strip() for origins in envs.BACKEND_CORS_ORIGINS.split(",") if origins.strip()]

logger.debug(f"CORS origins configured: {cors_origins}")

# Add regular CORS middleware first (runs last)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    max_age=86400,
)

# Add CORS logging middleware for debugging
app.add_middleware(CORSLoggingMiddleware)

# Add request/response logging middleware
app.add_middleware(LoggingMiddleware)

# Add selective CORS middleware last (runs first) - this will override regular CORS for specific endpoints
permissive_endpoints = [
    "/api/v1/organizations/get/setting/customize/public/bubble",  # Public chatbot endpoint (matches with path params)
    # Add more endpoints here that need permissive CORS
]
app.add_middleware(SelectiveCORSMiddleware, permissive_paths=permissive_endpoints)

@app.get("/")
async def root():
    logger.info("Root endpoint accessed - returning system status")
    logger.debug("Environment variables loaded:")
    logger.debug(f"POSTGRES_USER = {envs.POSTGRES_USER}")
    logger.debug(f"POSTGRES_DB = {envs.POSTGRES_DB}")
    logger.debug(f"POSTGRES_HOST = {envs.POSTGRES_HOST}")
    logger.debug(f"POSTGRES_PORT = {envs.POSTGRES_PORT}")
    # Don't log sensitive information like passwords and API keys
    logger.debug(f"PINECONE_ENV = {envs.PINECONE_ENV}")
    logger.debug(f"PINECONE_KNOWLEDGE_BASE_INDEX = {envs.PINECONE_KNOWLEDGE_BASE_INDEX}")
    logger.debug(f"REDIS_HOST = {envs.REDIS_HOST}")
    logger.debug(f"REDIS_PORT = {envs.REDIS_PORT}")
    logger.debug(f"FRONTEND_HOST = {envs.FRONTEND_HOST}")
    logger.debug(f"BUCKET_NAME = {envs.BUCKET_NAME}")

    
    return {
        "status": "ok", 
        "service": "Marti AI Backend",
        "version": "1.0.0",
        "environment": envs.ENVIRONMENT or "production"
    }

 
# Health check endpoint
@app.get("/health")
async def health():
    logger.debug("Health check endpoint accessed")
    return {
        "status": "healthy", 
        "service": "Marti AI Backend",
        "timestamp": __import__("datetime").datetime.utcnow().isoformat()
    }
 
# secure this endpoint with a secret key passed in query params
@app.post("/create-super-admin/")
async def create_super_admin_endpoint(db: Session = Depends(get_db)):
    logger.info("Create super admin endpoint accessed")
    try:
        # if secret_key != envs.SUPER_ADMIN_SECRET_KEY:
        #     logger.error("Unauthorized access to create super admin endpoint")
        #     raise HTTPException(status_code=401, detail="Unauthorized")
        response = create_super_admin(db)
        logger.info(f"Super admin creation response: {response}")
        return response
    except Exception as e:
        logger.error(f"Failed to create super admin: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create super admin")

    