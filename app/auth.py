"""Demo authentication boundary, intentionally replaceable with OIDC later."""

from dataclasses import dataclass
import hmac

from fastapi import Header, HTTPException, status

from app.config import settings


@dataclass(frozen=True)
class Principal:
    user_id: str
    authentication_type: str = "demo_api_key"


def get_principal(x_demo_api_key: str | None = Header(default=None)) -> Principal:
    """Authenticate the local demo key and return the server-owned identity."""
    if not x_demo_api_key or not hmac.compare_digest(x_demo_api_key, settings.DEMO_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid X-Demo-API-Key header is required.",
        )
    return Principal(user_id=settings.DEMO_USER_ID.strip().upper())
