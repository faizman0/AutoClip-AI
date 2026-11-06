"""
Unified backend application factory function
Supports web and desktop modes
"""
import logging
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend import __version__
from backend.api.v1 import api_router
from backend.api.v1.health import router as health_router
from backend.core.database import engine
from backend.models.base import Base
from backend.core.config import get_logging_config, get_api_key
from backend.core.error_middleware import global_exception_handler
from backend.core.local_origin_guard import LocalOriginGuard, allowed_origins

logger = logging.getLogger(__name__)

def create_app(mode: str = "web") -> FastAPI:
    """
    Create FastAPI application instance
    
    Args:
        mode: Run mode, supports "web" or "desktop"
    """
    # Set mode environment variable
    os.environ["AUTOCLIP_MODE"] = mode

    if mode == "desktop":
        from backend.core.windows_asyncio import install_windows_proactor_cleanup
        install_windows_proactor_cleanup()

    try:
        from backend.core.sentry_setup import init_sentry
        init_sentry(mode)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Sentry Initialization failed (ignored): {e}")
    
    # Configure logging
    logging_config = get_logging_config()
    logging.basicConfig(
        level=getattr(logging, logging_config["level"]),
        format=logging_config["format"],
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(logging_config["file"])
        ]
    )
    
    # Create the FastAPI app
    app = FastAPI(
        title="AutoClip API",
        description="AI video clip processing API",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc"
    )
    
    # Set application state
    app.state.mode = mode
    
    # Origin guard on the inside, CORS on the outside: preflight is answered by CORS, and write requests from other web pages are blocked by the guard.
    # It used to be allow_origins=["*"] + allow_credentials, so any web page could read the API Key in the settings.
    origins = allowed_origins()
    app.add_middleware(LocalOriginGuard, origins=origins, enforce_local_host=(mode == "desktop"))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Register the global exception handler
    app.add_exception_handler(Exception, global_exception_handler)
    
    # Startup event
    @app.on_event("startup")
    async def startup_event():
        logger.info(f"Starting AutoClip API service (mode: {mode})...")
        
        # Import all models to ensure tables are created
        from backend.models.bilibili import BilibiliAccount, UploadRecord
        Base.metadata.create_all(bind=engine)
        logger.info("Database table creation complete")
        try:
            from backend.core.project_enum_migration import normalize_legacy_project_enums
            changed = normalize_legacy_project_enums(engine)
            if changed:
                logger.info("Rewrote legacy enum values in %s project records to values recognized by the current version", changed)
        except Exception:
            logger.exception("Failed to clean up legacy project enum values; list reads will use degraded values")
        try:
            # Studio tasks still running when the previous process was terminated will not continue: mark them as failed with the reason stated (RC156 #13)
            from backend.services.studio.restart_recovery import reconcile_interrupted_projects
            reconcile_interrupted_projects()
        except Exception:
            logger.exception("Studio restart recovery failed; it will be retried when the project is opened")
        try:
            # Only clean up leftover directories under the projects root that have no corresponding row in the database; if reading the database fails, delete nothing (RC156 PR4)
            from backend.services.project_orphan_cleanup import cleanup_orphan_project_directories
            cleanup_orphan_project_directories()
        except Exception:
            logger.exception("Failed to clean up leftover project directories with no database row")
        
        # Load the API key into environment variables
        api_key = get_api_key()
        if api_key:
            os.environ["DASHSCOPE_API_KEY"] = api_key
            logger.info("API key loaded into environment variables")
        else:
            logger.warning("No API key configuration found")
        
        # Perform different initialization depending on the mode
        if mode == "desktop":
            logger.info("Desktop mode: using local queue and SQLite")
        else:
            logger.info("Web mode: using Redis/Celery")
        
        logger.info("WebSocket gateway service disabled; using the new simplified progress system")
    
    # Shutdown event
    @app.on_event("shutdown")
    async def shutdown_event():
        logger.info("Shutting down AutoClip API service...")
        from backend.services import child_guard
        child_guard.kill_all()
        logger.info("WebSocket gateway service disabled")
    
    # Register routes
    app.include_router(health_router, prefix="/api/health", tags=["health"])
    app.include_router(api_router, prefix="/api/v1")
    
    # Add video-categories routes (unified into api_router)
    @app.get("/api/v1/video-categories")
    async def get_video_categories():
        """Get video category configuration."""
        return {
            "categories": [
                {
                    "value": "default",
                    "name": "Default",
                    "description": "General video content processing",
                    "icon": "🎬",
                    "color": "#4facfe"
                },
                {
                    "value": "knowledge",
                    "name": "Knowledge & Science",
                    "description": "Knowledge content such as science, technology, history, and culture",
                    "icon": "📚",
                    "color": "#52c41a"
                },
                {
                    "value": "entertainment",
                    "name": "Entertainment",
                    "description": "Entertainment content such as games, music, and movies",
                    "icon": "🎮",
                    "color": "#722ed1"
                },
                {
                    "value": "business",
                    "name": "Business",
                    "description": "Business content such as business, startups, and investment",
                    "icon": "💼",
                    "color": "#fa8c16"
                },
                {
                    "value": "experience",
                    "name": "Experience Sharing",
                    "description": "Experience content such as personal experiences and life reflections",
                    "icon": "🌟",
                    "color": "#eb2f96"
                },
                {
                    "value": "opinion",
                    "name": "Opinion & Commentary",
                    "description": "Commentary content such as current affairs commentary and opinion analysis",
                    "icon": "💭",
                    "color": "#13c2c2"
                },
                {
                    "value": "speech",
                    "name": "Speech",
                    "description": "Speech content such as public speeches and lectures",
                    "icon": "🎤",
                    "color": "#f5222d"
                }
            ],
            "default_category": "default"
        }
    
    # Root health check
    @app.get("/health")
    async def root_health():
        try:
            return {
                "status": "ok",
                "mode": mode,
                "version": __version__
            }
        except Exception as e:
            return JSONResponse(
                status_code=500, 
                content={"status": "error", "detail": str(e)}
            )
    
    return app
