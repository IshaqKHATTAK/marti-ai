import logging
import logging.config
import sys
from pathlib import Path
from typing import Optional
from app.common.env_config import get_envs_setting

def setup_logging(log_level: Optional[str] = None, log_file: Optional[str] = None) -> None:
    """
    Setup application-wide logging configuration.
    
    Args:
        log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional log file path. If None, logs only to console.
    """
    
    envs = get_envs_setting()
    
    # Determine log level
    if log_level is None:
        log_level = "DEBUG" if envs.DEBUG else "INFO"  # Less verbose by default
    
    # Create logs directory if using file logging
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Logging configuration
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "detailed": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(funcName)s:%(lineno)d - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S"
            },
            "simple": {
                "format": "%(levelname)s - %(message)s"
            },
            "json": {
                "format": "%(asctime)s %(name)s %(levelname)s %(module)s %(funcName)s %(lineno)d %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S"
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": log_level,
                "formatter": "detailed",
                "stream": sys.stdout
            }
        },
        "root": {
            "level": log_level,
            "handlers": ["console"]
        },
        "loggers": {
            "fastapi": {
                "level": "INFO",
                "handlers": ["console"],
                "propagate": False
            },
            "uvicorn": {
                "level": "INFO",
                "handlers": ["console"],  
                "propagate": False
            },
            "uvicorn.access": {
                "level": "WARNING",
                "handlers": ["console"],
                "propagate": False
            },
            "sqlalchemy.engine": {
                "level": "WARNING",  # Reduce SQLAlchemy noise
                "handlers": ["console"],
                "propagate": False
            },
            "python_multipart": {
                "level": "INFO",  # Reduce multipart parsing noise
                "handlers": ["console"],
                "propagate": False
            },
            "httpcore": {
                "level": "INFO",  # Reduce HTTP client noise
                "handlers": ["console"],
                "propagate": False
            },
            "httpx": {
                "level": "INFO",  # Reduce HTTP client noise
                "handlers": ["console"],
                "propagate": False
            },
            "stripe": {
                "level": "INFO",  # Keep Stripe logs at INFO level
                "handlers": ["console"],
                "propagate": False
            },
            "app": {
                "level": log_level,
                "handlers": ["console"],
                "propagate": False
            }
        }
    }
    
    # Add file handler if log_file is specified
    if log_file:
        config["handlers"]["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": log_level,
            "formatter": "detailed",
            "filename": log_file,
            "maxBytes": 10485760,  # 10MB
            "backupCount": 5,
            "encoding": "utf-8"
        }
        
        # Add file handler to all loggers
        for logger_name in config["loggers"]:
            config["loggers"][logger_name]["handlers"].append("file")
        config["root"]["handlers"].append("file")
    
    # Apply the configuration
    logging.config.dictConfig(config)
    
    # Log the initialization
    logger = logging.getLogger("app.logging")
    logger.info(f"Logging initialized with level: {log_level}")
    if log_file:
        logger.info(f"File logging enabled: {log_file}")


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for the specified module/name.
    
    Args:
        name: Logger name (typically __name__ from the calling module)
        
    Returns:
        logging.Logger: Configured logger instance
    """
    return logging.getLogger(f"app.{name}")


