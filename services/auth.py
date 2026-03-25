"""JWT authentication for the dashboard API."""

from __future__ import annotations

import time
import logging
from typing import Optional

import jwt
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from config.settings import settings

logger = logging.getLogger(__name__)

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Token expiration: 24 hours
TOKEN_EXPIRY_SECONDS = 86400


def create_token() -> str:
    """Create a JWT token."""
    payload = {
        "sub": "dashboard",
        "iat": int(time.time()),
        "exp": int(time.time()) + TOKEN_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, settings.dashboard.jwt_secret, algorithm="HS256")


def verify_token(token: str) -> dict:
    """Verify and decode a JWT token. Raises HTTPException on failure."""
    try:
        return jwt.decode(
            token, settings.dashboard.jwt_secret, algorithms=["HS256"]
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def require_auth(
    api_key: Optional[str] = Security(_api_key_header),
) -> dict:
    """FastAPI dependency: validates API key or JWT bearer token.

    Usage:
        @app.get("/api/data")
        async def data(auth: dict = Depends(require_auth)):
            ...
    """
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key or token")

    # Check if it's a static API key (for simple auth)
    if api_key == settings.dashboard.api_key:
        return {"sub": "api_key", "role": "admin"}

    # Otherwise, try JWT
    return verify_token(api_key)
