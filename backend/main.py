"""
Edition Corrector — FastAPI backend
Run with: uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from backend.config import settings
from backend.database import init_db
from backend.routers import jobs, upload
from backend.routers import auth as auth_router
from backend.routers import admin as admin_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

# Rate limiter — uses client IP as the key.
# Set RATE_LIMIT_ENABLED=false in .env to disable during local dev.
limiter = Limiter(
    key_func=get_remote_address,
    enabled=settings.RATE_LIMIT_ENABLED,
)

app = FastAPI(
    title="Edition Corrector",
    description="SaaS de correction éditoriale PDF avec annotations natives",
    version="1.0.0",
    # Disable automatic OpenAPI docs in production to reduce attack surface.
    # Re-enable locally by setting DOCS_ENABLED=true (see below).
)

# Attach rate limiter state and error handler.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS — driven by ALLOWED_ORIGINS in .env so no code changes are needed per environment.
# Never use allow_origins=["*"] for credentialed requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
    expose_headers=["X-Page-Width-Pts", "X-Page-Height-Pts", "X-Page-Rendered", "X-Total-Pages"],
)

app.include_router(auth_router.router)
app.include_router(admin_router.router)
app.include_router(upload.router)
app.include_router(jobs.router)


@app.on_event("startup")
def on_startup():
    _log = logging.getLogger(__name__)
    if settings.JWT_SECRET == "CHANGE_ME_set_JWT_SECRET_in_env_file":
        # En production (base non-SQLite = Railway/Postgres), un secret par défaut
        # permettrait à quiconque de forger des tokens admin → refus de démarrer.
        if not settings.DATABASE_URL.startswith("sqlite"):
            raise RuntimeError(
                "JWT_SECRET non configuré en production. "
                "Définissez JWT_SECRET (longue chaîne aléatoire) dans les variables d'environnement."
            )
        _log.warning(
            "⚠ JWT_SECRET non configuré — utilisez une valeur secrète dans .env avant la mise en production !"
        )
    init_db()
    _log.info(
        "Database initialized. CORS origins: %s | Rate limiting: %s",
        settings.allowed_origins_list(),
        settings.RATE_LIMIT_ENABLED,
    )
    _recover_orphan_jobs()
    _ensure_admin()


# Statuts transitoires : un job dans cet état au démarrage est forcément orphelin
# (son asyncio.Task est mort avec l'ancien process — redéploiement ou crash).
# "awaiting_confirmation" est exclu : c'est un état stable (attente utilisateur).
_ORPHAN_STATUSES = ("pending", "extracting", "processing", "annotating")


def _recover_orphan_jobs():
    """
    Marque en erreur les jobs interrompus par un redémarrage du serveur.
    Sans cela ils restent bloqués en "processing" pour toujours et l'utilisateur
    ne voit ni erreur ni résultat. Les crédits ne sont débités qu'à la fin du
    pipeline, donc aucun remboursement n'est nécessaire.
    """
    from backend.database import SessionLocal
    from backend.models import Job
    _log = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        orphans = db.query(Job).filter(Job.status.in_(_ORPHAN_STATUSES)).all()
        for job in orphans:
            job.status = "error"
            job.error_message = (
                "Traitement interrompu par un redémarrage du serveur. "
                "Relancez l'analyse — aucun crédit n'a été débité."
            )
            job.progress_label = "Interrompu (redémarrage serveur)"
        if orphans:
            db.commit()
            _log.warning(
                "Watchdog : %d job(s) orphelin(s) marqué(s) en erreur : %s",
                len(orphans), [j.id for j in orphans],
            )
    except Exception as exc:
        _log.warning("Watchdog jobs orphelins : %s (non bloquant)", exc)
    finally:
        db.close()


def _ensure_admin():
    """Create initial admin account if ADMIN_EMAIL + ADMIN_PASSWORD are set and no admin exists."""
    if not settings.ADMIN_EMAIL or not settings.ADMIN_PASSWORD:
        return
    from backend.database import SessionLocal
    from backend.models import User
    from backend.auth import hash_password
    db = SessionLocal()
    try:
        exists = db.query(User).filter(User.email == settings.ADMIN_EMAIL.lower()).first()
        if not exists:
            from datetime import datetime
            admin = User(
                email=settings.ADMIN_EMAIL.lower(),
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                name="Admin",
                role="admin",
                monthly_limit_usd=0.0,  # unlimited
                last_reset_at=datetime.utcnow(),
            )
            db.add(admin)
            db.commit()
            logging.getLogger(__name__).info("Compte admin créé : %s", settings.ADMIN_EMAIL)
    except Exception as exc:
        logging.getLogger(__name__).warning("Impossible de créer le compte admin : %s", exc)
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}
