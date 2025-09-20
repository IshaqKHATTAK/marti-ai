from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from app.common.logging_config import get_logger

logger = get_logger("selective_cors")


class SelectiveCORSMiddleware(BaseHTTPMiddleware):
    """
    Middleware to apply permissive CORS only to specific endpoints.
    """
    
    def __init__(self, app: ASGIApp, permissive_paths: list[str] = None):
        super().__init__(app)
        self.permissive_paths = permissive_paths or []
        
    def is_permissive_path(self, path: str) -> bool:
        """Check if the path should have permissive CORS."""
        return any(path.startswith(perm_path) for perm_path in self.permissive_paths)
    
    async def dispatch(self, request: Request, call_next):
        # Get the request path
        path = request.url.path
        
        # Check if this path needs permissive CORS
        if self.is_permissive_path(path):
            # Handle preflight requests BEFORE processing
            if request.method == "OPTIONS":
                from fastapi import Response
                response = Response()
                response.headers["Access-Control-Allow-Origin"] = "*"
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
                response.headers["Access-Control-Allow-Headers"] = "*"
                response.headers["Access-Control-Max-Age"] = "86400"
                response.headers["Access-Control-Allow-Credentials"] = "false"
                return response
            
            # For non-preflight requests, process normally then add CORS headers
            response = await call_next(request)
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"  
            response.headers["Access-Control-Allow-Headers"] = "*"
            response.headers["Access-Control-Max-Age"] = "86400"
            return response
        else:
            # For non-permissive paths, process normally
            response = await call_next(request)
            return response
