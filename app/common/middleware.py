import time
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from app.common.logging_config import get_logger

logger = get_logger("middleware")


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware to log all incoming requests and outgoing responses.
    """
    
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        
    async def dispatch(self, request: Request, call_next):
        # Start timing
        start_time = time.time()
        
        # Get request details
        method = request.method
        path = request.url.path
        query_params = str(request.query_params) if request.query_params else None
        client_ip = request.client.host if request.client else "unknown"
        
        # Only log incoming requests for errors or debug mode
        if query_params:
            logger.debug(f"Query parameters: {query_params}")
            
        # Process request
        try:
            response = await call_next(request)
            
            # Calculate processing time
            process_time = time.time() - start_time
            
            # Only log errors or slow requests
            if response.status_code >= 400:
                logger.warning(
                    f"Error: {method} {path} - {response.status_code} - "
                    f"{process_time:.3f}s from {client_ip}"
                )
            elif process_time > 1.0:  # Log slow requests (>1 second)
                logger.info(f"Slow request: {method} {path} - {process_time:.3f}s")
            
            return response
            
        except Exception as exc:
            # Calculate processing time for errors
            process_time = time.time() - start_time
            
            # Log the exception
            logger.error(
                f"Request failed: {method} {path} - {exc.__class__.__name__}: {str(exc)} - "
                f"{process_time:.3f}s from {client_ip}"
            )
            
            # Re-raise the exception
            raise exc


class CORSLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware to log CORS requests for debugging purposes.
    """
    
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        
    async def dispatch(self, request: Request, call_next):
        # Check if this is a CORS preflight request
        is_preflight = request.method == "OPTIONS"
        origin = request.headers.get("origin")
        
        if origin and is_preflight:
            # Only log preflight requests, not every CORS request
            logger.debug(f"CORS preflight: {origin} -> {request.url.path}")
        
        # Process the request
        response = await call_next(request)
        
        # Only log CORS headers in debug mode and for preflight requests
        if origin and is_preflight:
            cors_headers = {
                k: v for k, v in response.headers.items() 
                if k.lower().startswith('access-control-')
            }
            if cors_headers:
                logger.debug(f"CORS headers for {request.url.path}: {cors_headers}")
        
        return response
