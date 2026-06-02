"""
Upload endpoint: receives a PDF, runs extraction + cost estimate,
waits for user confirmation, then starts the pipeline.
"""
from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

import fitz  # PyMuPDF — lecture des métadonnées embarquées
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.config import settings
from backend.database import get_db
from backend.models import Job, User
from backend.services.pdf_extractor import estimate_cost, extract
from backend.services.pipeline import run_pipeline

limiter = Limiter(key_func=get_remote_address, enabled=settings.RATE_LIMIT_ENABLED)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["upload"])

MAX_FILE_BYTES = settings.MAX_FILE_SIZE_MB * 1024 * 1024

# PDF magic bytes: %PDF
_PDF_MAGIC = b"%PDF"

VALID_DOC_TYPES = {
    "roman", "bd_comics", "jeunesse", "poesie_theatre",
    "documentaire", "beaux_arts", "tourisme", "cuisine", "sport",
    "manuel_scolaire", "parascolaire", "essai",
    "magazine", "revue_presse",
    "autre",
}

PRESET_CATEGORIES = {
    "complete": list("ABCDEFGH"),
    "quick":    ["A", "B", "C", "D"],
    "coherence": ["E", "F", "G"],
    "facts":    ["H"],
}


class ConfirmMetadata(BaseModel):
    author: str | None = None
    title: str | None = None
    characters: str | None = None
    citation_lang: str | None = None
    house_rules: str | None = None


class ConfirmBody(BaseModel):
    preset: str = "complete"
    metadata: ConfirmMetadata = ConfirmMetadata()
    comment_mode: str = "detailed"   # "simple" | "detailed"
    generate_pdf: bool = True        # False → skip annotation, DOCX only


@router.post("/upload")
@limiter.limit(settings.UPLOAD_RATE_LIMIT)
async def upload_pdf(
    request: Request,
    file: UploadFile,
    doc_type: str = Form(default="autre"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Step 1 — Upload PDF and return cost estimate.
    Returns job_id and estimate. The job is paused until /confirm is called.
    """
    # --- Extension check ---
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Le fichier doit être un PDF.")

    # --- MIME type check (Content-Type header) ---
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Le type MIME doit être application/pdf.")

    # Validate doc_type
    if doc_type not in VALID_DOC_TYPES:
        doc_type = "autre"

    # Read file content and check size
    content = await file.read()
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Fichier trop volumineux (max {settings.MAX_FILE_SIZE_MB} Mo).",
        )

    # --- Magic bytes check: must start with %PDF ---
    if not content.startswith(_PDF_MAGIC):
        raise HTTPException(status_code=400, detail="Le fichier n'est pas un PDF valide.")

    # --- Sanitize filename: strip directory components and non-safe characters ---
    raw_name = Path(file.filename).name  # removes any ../ traversal in filename
    safe_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
    safe_name = "".join(c if c in safe_chars else "_" for c in raw_name) or "document.pdf"
    if not safe_name.lower().endswith(".pdf"):
        safe_name = safe_name + ".pdf"

    # Save to uploads directory — job_id is a UUID so path is safe
    job_id = str(uuid.uuid4())
    upload_dir = Path(settings.UPLOAD_DIR).resolve() / job_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = upload_dir / safe_name

    with open(pdf_path, "wb") as f:
        f.write(content)

    # Extract text synchronously (fast, just for estimate)
    try:
        extraction = extract(str(pdf_path))
    except Exception as exc:
        shutil.rmtree(upload_dir, ignore_errors=True)
        logger.error("PDF extraction failed for job %s", job_id)
        raise HTTPException(status_code=422, detail="Impossible de lire le PDF : le fichier est corrompu ou protégé.")

    # Read embedded PDF metadata (author/title) for pre-filling the modal.
    # Values that are blank or typical software defaults are discarded.
    _JUNK_META = {"", "unknown", "microsoft word", "adobe acrobat", "adobe",
                  "untitled", "author", "word", "libreoffice", "writer"}
    pdf_meta_author = ""
    pdf_meta_title = ""
    try:
        _doc = fitz.open(str(pdf_path))
        _meta = _doc.metadata or {}
        _doc.close()
        _raw_author = (_meta.get("author") or "").strip()
        _raw_title  = (_meta.get("title")  or "").strip()
        if _raw_author.lower() not in _JUNK_META and len(_raw_author) > 2:
            pdf_meta_author = _raw_author
        if _raw_title.lower() not in _JUNK_META and len(_raw_title) > 2:
            pdf_meta_title = _raw_title
    except Exception:
        pass  # metadata read is best-effort — never block the upload

    # Estimate cost
    cost_info = estimate_cost(
        extraction,
        input_price=settings.CLAUDE_INPUT_PRICE,
        output_price=settings.CLAUDE_OUTPUT_PRICE,
    )

    # Create job in DB
    job = Job(
        id=job_id,
        filename=safe_name,
        status="awaiting_confirmation",
        progress=0,
        progress_label="En attente de confirmation…",
        pages_count=extraction.total_pages,
        word_count=extraction.total_words,
        estimated_tokens=cost_info["estimated_tokens"],
        estimated_cost_usd=cost_info["estimated_cost_usd"],
        input_pdf_path=str(pdf_path),
        doc_type=doc_type,
        user_id=current_user.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    return JSONResponse({
        "job_id": job_id,
        "filename": job.filename,
        "pages": extraction.total_pages,
        "words": extraction.total_words,
        "estimated_tokens": cost_info["estimated_tokens"],
        "estimated_cost_usd": cost_info["estimated_cost_usd"],
        "estimated_corrections": cost_info["estimated_corrections"],
        "doc_type": doc_type,
        "status": "awaiting_confirmation",
        "pdf_metadata": {"author": pdf_meta_author, "title": pdf_meta_title},
    })


@router.post("/jobs/{job_id}/confirm")
@limiter.limit(settings.CONFIRM_RATE_LIMIT)
async def confirm_job(
    request: Request,
    job_id: str,
    background_tasks: BackgroundTasks,
    body: ConfirmBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Step 2 — User confirms the cost estimate, pipeline starts.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable.")
    if current_user.role != "admin" and job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Accès interdit.")

    if job.status not in ("awaiting_confirmation",):
        raise HTTPException(
            status_code=409,
            detail=f"Ce job ne peut pas être confirmé (statut : {job.status}).",
        )

    # ── Credit check (hard limit) ─────────────────────────────────────────────
    from backend.auth import maybe_reset_credits
    maybe_reset_credits(current_user, db)
    limit = current_user.monthly_limit_usd or 0.0
    if limit > 0 and (current_user.current_month_spend_usd or 0.0) >= limit:
        eur = settings.EUR_PER_USD
        raise HTTPException(
            status_code=402,
            detail=(
                f"Limite mensuelle atteinte ({round(limit * eur, 2):.2f}€ / mois). "
                "Contactez l'administrateur pour augmenter votre quota."
            ),
        )

    job.status = "pending"
    job.confirmed = True
    job.progress_label = "Démarrage du traitement…"

    preset = body.preset if body.preset in PRESET_CATEGORIES else "complete"
    enabled_cats = PRESET_CATEGORIES[preset]
    job.enabled_categories = enabled_cats
    job.metadata_author = body.metadata.author
    job.metadata_title = body.metadata.title
    job.metadata_characters = body.metadata.characters
    job.metadata_citation_lang = body.metadata.citation_lang
    job.generate_pdf = body.generate_pdf
    db.commit()

    # Launch pipeline as background task, with doc_type and preset settings
    doc_type = job.doc_type or "autre"
    metadata_dict = {
        "author": body.metadata.author,
        "title": body.metadata.title,
        "characters": body.metadata.characters,
        "citation_lang": body.metadata.citation_lang,
        "house_rules": body.metadata.house_rules,
    }
    comment_mode = body.comment_mode if body.comment_mode in ("simple", "detailed") else "detailed"
    background_tasks.add_task(
        run_pipeline, job_id, job.input_pdf_path, doc_type,
        enabled_categories=enabled_cats,
        metadata=metadata_dict,
        comment_mode=comment_mode,
        generate_pdf=body.generate_pdf,
        user_id=current_user.id,
    )

    return JSONResponse({"job_id": job_id, "status": "processing_started"})


@router.delete("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel a job that is awaiting confirmation."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable.")

    if job.status == "awaiting_confirmation":
        import shutil
        if job.input_pdf_path:
            shutil.rmtree(Path(job.input_pdf_path).parent, ignore_errors=True)
        db.delete(job)
        db.commit()
        return JSONResponse({"deleted": True})

    raise HTTPException(status_code=409, detail="Impossible d'annuler ce job.")
