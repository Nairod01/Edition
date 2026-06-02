"""
Authentication endpoints: login, me, change-password.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth import (
    create_token, get_current_user, hash_password,
    maybe_reset_credits, verify_password,
)
from backend.config import settings
from backend.database import get_db
from backend.models import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_dict(user: User) -> dict:
    limit = user.monthly_limit_usd or 0.0
    spent = user.current_month_spend_usd or 0.0
    remaining = max(0.0, limit - spent) if limit > 0 else None
    eur = settings.EUR_PER_USD
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "is_active": user.is_active,
        "monthly_limit_usd": limit,
        "current_month_spend_usd": round(spent, 4),
        "credits_remaining_usd": round(remaining, 4) if remaining is not None else None,
        "spent_eur": round(spent * eur, 2),
        "limit_eur": round(limit * eur, 2) if limit > 0 else None,
        "credits_remaining_eur": round(remaining * eur, 2) if remaining is not None else None,
    }


class LoginBody(BaseModel):
    email: str
    password: str


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


@router.post("/login")
async def login(body: LoginBody, db: Session = Depends(get_db)):
    """Authenticate with email + password, return JWT."""
    user = db.query(User).filter(
        User.email == body.email.lower().strip(),
        User.is_active == True,  # noqa: E712
    ).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect.",
        )
    maybe_reset_credits(user, db)
    user.last_login_at = datetime.utcnow()
    db.commit()
    token = create_token(user.id, settings.JWT_SECRET)
    logger.info("Connexion — %s (%s)", user.email, user.role)
    return JSONResponse({
        "access_token": token,
        "token_type": "bearer",
        "user": _user_dict(user),
    })


@router.get("/me")
async def me(current_user=Depends(get_current_user)):
    """Return current user info (also refreshes credit counter)."""
    return _user_dict(current_user)


@router.post("/change-password")
async def change_password(
    body: ChangePasswordBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Mot de passe actuel incorrect.")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=422, detail="Le mot de passe doit contenir au moins 8 caractères.")
    current_user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"status": "password_changed"}
