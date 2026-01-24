"""Storage factory - returns appropriate storage implementation based on configuration.

This module provides a unified interface for accessing either PostgreSQL or SQLite storage,
automatically selecting the appropriate implementation based on the DATABASE_URL environment variable.
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def get_store() -> Any:
    """Get the appropriate storage implementation based on configuration.

    Returns either PostgreSQL or SQLite storage module based on DATABASE_URL:
    - If DATABASE_URL is set and starts with 'postgresql://', returns pg_store
    - Otherwise, returns sqlite_store (default fallback)

    Returns:
        Storage module (either pg_store or sqlite_store)
    """
    database_url = os.environ.get("DATABASE_URL", "")

    if database_url and database_url.startswith("postgresql://"):
        logger.info("Using PostgreSQL storage (DATABASE_URL configured)")
        try:
            from storage import pg_store
            return pg_store
        except ImportError as e:
            logger.error("Failed to import PostgreSQL storage: %s", e)
            logger.warning("Falling back to SQLite storage")
            from storage import sqlite_store
            return sqlite_store
    else:
        logger.info("Using SQLite storage (default)")
        from storage import sqlite_store
        return sqlite_store


def get_database_url() -> str:
    """Get the database URL for PostgreSQL or None for SQLite.

    Returns:
        Database URL string for PostgreSQL, or empty string for SQLite
    """
    database_url = os.environ.get("DATABASE_URL", "")

    if database_url and database_url.startswith("postgresql://"):
        return database_url
    else:
        return ""


def is_postgres() -> bool:
    """Check if PostgreSQL storage is configured.

    Returns:
        True if using PostgreSQL, False if using SQLite
    """
    database_url = os.environ.get("DATABASE_URL", "")
    return database_url.startswith("postgresql://")
