"""Authentication API endpoints for login, token refresh, and current user.

Provides endpoints for user login (email/password), JWT token refresh,
and retrieving the current authenticated user's profile.
"""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse, UserResponse
from app.services.auth_service import (
    REFRESH_TOKEN_TYPE,
    authenticate_user,
    create_access_token,
    create_refresh_token,
    decode_token,
    validate_token_type,
)
from app.storage.database import get_db

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])

_bearer = HTTPBearer()


@router.get(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current authenticated user",
)
async def get_me(
    db: Annotated[AsyncSession, Depends(get_db)],
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> UserResponse:
    """Return the currently authenticated user's profile."""
    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject",
        )
    user = await _get_active_user(db, user_id)
    return UserResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        org_id=str(user.org_id),
        created_at=str(user.created_at),
        updated_at=str(user.updated_at),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate user and obtain tokens",
)
async def login(
    request: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """Authenticate a user with email and password.

    Returns a JWT access/refresh token pair on success.
    """
    logger.info("login_attempt", email=request.email)

    user = await authenticate_user(db, request.email, request.password)
    if user is None:
        logger.warning("login_failed", email=request.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_data = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "org_id": str(user.org_id),
    }

    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    logger.info("login_success", user_id=str(user.id), email=user.email)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Refresh access token",
)
async def refresh_token(
    request: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """Exchange a valid refresh token for a new token pair."""
    payload = decode_token(request.refresh_token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    if not validate_token_type(payload, REFRESH_TOKEN_TYPE):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
        )

    user = await _get_active_user(db, user_id)

    token_data = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "org_id": str(user.org_id),
    }

    new_access = create_access_token(token_data)
    new_refresh = create_refresh_token(token_data)

    logger.info("token_refreshed", user_id=str(user.id))

    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    summary="Logout current user",
)
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """Logout the current user.

    With stateless JWT authentication, server-side logout is a no-op.
    The client should discard the token. This endpoint exists so the
    frontend has a concrete endpoint to call on logout.

    Returns:
        Confirmation message.
    """
    logger.info("user_logged_out")
    return {"detail": "Successfully logged out"}


async def _get_active_user(db: AsyncSession, user_id: str) -> "User":
    """Fetch an active user by ID."""
    import uuid as uuid_mod

    from sqlalchemy import select

    from app.models.user import User

    try:
        uid = uuid_mod.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID",
        )
    stmt = select(User).where(User.id == uid)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    return user
