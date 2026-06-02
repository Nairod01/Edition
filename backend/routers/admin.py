"""
Admin endpoints: user management, global stats, per-user jobs.
Only accessible to users with role="admin".
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.auth import get_admin_user, get_current_user, hash_password, maybe_reset_credits
from backend.config import settings
from backend.database import get_db
from backend.models import Correction, CorrectionFeedback, Job, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])

EUR = lambda usd: round((usd or 0) * settings.EUR_PER_USD, 2)  # noqa: E731


def _user_dict(user: User, db: Session) -> dict:
    limit = user.monthly_limit_usd or 0.0
    spent = user.current_month_spend_usd or 0.0
    remaining = max(0.0, limit - spent) if limit > 0 else None
    job_count = db.query(func.count(Job.id)).filter(Job.user_id == user.id).scalar() or 0
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "is_active": user.is_active,
        "monthly_limit_usd": limit,
        "current_month_spend_usd": round(spent, 4),
        "credits_remaining_usd": round(remaining, 4) if remaining is not None else None,
        "spent_eur": EUR(spent),
        "limit_eur": EUR(limit) if limit > 0 else None,
        "credits_remaining_eur": EUR(remaining) if remaining is not None else None,
        "jobs_count": job_count,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


# ── User listing ────────────────────────────────────────────────────────────

@router.get("/users")
def list_users(db: Session = Depends(get_db), _: User = Depends(get_admin_user)):
    users = db.query(User).order_by(User.created_at).all()
    for u in users:
        maybe_reset_credits(u, db)
    return {"users": [_user_dict(u, db) for u in users]}


# ── Create user ─────────────────────────────────────────────────────────────

class CreateUserBody(BaseModel):
    email: str
    password: str
    name: str | None = None
    role: str = "user"
    monthly_limit_usd: float | None = None


@router.post("/users")
def create_user(
    body: CreateUserBody,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    email = body.email.lower().strip()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="Un compte avec cet email existe déjà.")
    if len(body.password) < 8:
        raise HTTPException(status_code=422, detail="Le mot de passe doit contenir au moins 8 caractères.")
    if body.role not in ("user", "admin"):
        raise HTTPException(status_code=422, detail="Rôle invalide (user ou admin).")

    limit = body.monthly_limit_usd if body.monthly_limit_usd is not None else (
        0.0 if body.role == "admin" else settings.DEFAULT_MONTHLY_LIMIT_USD
    )
    user = User(
        email=email,
        password_hash=hash_password(body.password),
        name=body.name,
        role=body.role,
        monthly_limit_usd=limit,
        last_reset_at=datetime.utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("Nouvel utilisateur créé — %s (%s)", email, body.role)
    return _user_dict(user, db)


# ── Update user ─────────────────────────────────────────────────────────────

class UpdateUserBody(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    monthly_limit_usd: float | None = None
    role: str | None = None
    new_password: str | None = None


@router.patch("/users/{user_id}")
def update_user(
    user_id: str,
    body: UpdateUserBody,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_admin_user),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    if body.name is not None:
        user.name = body.name
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.monthly_limit_usd is not None:
        user.monthly_limit_usd = max(0.0, body.monthly_limit_usd)
    if body.role is not None:
        if body.role not in ("user", "admin"):
            raise HTTPException(status_code=422, detail="Rôle invalide.")
        user.role = body.role
    if body.new_password:
        if len(body.new_password) < 8:
            raise HTTPException(status_code=422, detail="Mot de passe trop court (8 car. min).")
        user.password_hash = hash_password(body.new_password)
    db.commit()
    db.refresh(user)
    logger.info("Utilisateur mis à jour — %s par admin %s", user.email, current_admin.email)
    return _user_dict(user, db)


# ── Delete user ─────────────────────────────────────────────────────────────

@router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_admin_user),
):
    if user_id == current_admin.id:
        raise HTTPException(status_code=400, detail="Vous ne pouvez pas supprimer votre propre compte.")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    email = user.email
    db.delete(user)
    db.commit()
    logger.info("Utilisateur supprimé — %s par admin %s", email, current_admin.email)
    return JSONResponse({"deleted": True})


# ── User's job history (admin view) ─────────────────────────────────────────

@router.get("/users/{user_id}/jobs")
def user_jobs(
    user_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    jobs = (
        db.query(Job)
        .filter(Job.user_id == user_id)
        .order_by(Job.created_at.desc())
        .limit(100)
        .all()
    )

    fp_counts_q = (
        db.query(CorrectionFeedback.job_id, func.count(CorrectionFeedback.id))
        .filter(CorrectionFeedback.job_id.in_([j.id for j in jobs]))
        .group_by(CorrectionFeedback.job_id)
        .all()
    )
    fp_counts = {jid: cnt for jid, cnt in fp_counts_q}

    return {
        "user": _user_dict(user, db),
        "jobs": [
            {
                "id": j.id,
                "filename": j.filename,
                "status": j.status,
                "corrections_count": j.corrections_count or 0,
                "false_positives_count": fp_counts.get(j.id, 0),
                "doc_type": j.doc_type or "autre",
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "actual_cost_usd": j.actual_cost_usd,
                "actual_cost_eur": EUR(j.actual_cost_usd) if j.actual_cost_usd else None,
            }
            for j in jobs
        ],
    }


# ── Reset user credits manually ──────────────────────────────────────────────

@router.post("/users/{user_id}/reset-credits")
def reset_credits(
    user_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    user.current_month_spend_usd = 0.0
    user.last_reset_at = datetime.utcnow()
    db.commit()
    return {"status": "credits_reset", "user_id": user_id}
