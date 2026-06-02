"""
Job status, corrections listing, PDF download, and DOCX export endpoints.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.auth import get_admin_user, get_current_user
from backend.database import get_db
from backend.models import Correction, CorrectionFeedback, CorrectionFeedbackReason, Job, User
from backend.services.docx_exporter import export_corrections_docx


def _get_job(job_id: str, current_user: User, db: Session) -> Job:
    """Fetch a job and verify the current user owns it (admins bypass the check)."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable.")
    if current_user.role != "admin" and job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Accès interdit.")
    return job

# Debug endpoints are only available when DEBUG_ENDPOINTS=true is set in the environment.
# Never enable in production.
_DEBUG_ENABLED = os.getenv("DEBUG_ENDPOINTS", "false").lower() == "true"

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Poll job status and progress."""
    job = _get_job(job_id, current_user, db)

    # Always recount corrections live so the UI reflects reality
    cat_counts = (
        db.query(Correction.category, func.count(Correction.id))
        .filter(Correction.job_id == job_id)
        .group_by(Correction.category)
        .all()
    )
    by_category = {cat: cnt for cat, cnt in cat_counts}
    total = sum(by_category.values())

    # Only surface a sanitised error flag — never expose raw exception strings to clients.
    has_error = job.status == "error"

    return {
        "id": job.id,
        "filename": job.filename,
        "status": job.status,
        "progress": job.progress,
        "progress_label": job.progress_label,
        "pages_count": job.pages_count,
        "word_count": job.word_count,
        "estimated_cost_usd": job.estimated_cost_usd,
        "corrections_count": total,
        "corrections_by_category": by_category,
        "error_message": "Une erreur est survenue lors du traitement." if has_error else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "doc_type": job.doc_type or "autre",
        "annotated_count": job.annotated_count or total,
        "h_not_annotated_count": job.h_not_annotated_count or 0,
        "generate_pdf": job.generate_pdf if job.generate_pdf is not None else True,
        "actual_cost_usd": job.actual_cost_usd,
    }


@router.get("/jobs/{job_id}/corrections")
def get_corrections(job_id: str, category: str | None = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """List all corrections for a job, optionally filtered by category."""
    job = _get_job(job_id, current_user, db)

    query = db.query(Correction).filter(Correction.job_id == job_id)
    if category:
        query = query.filter(Correction.category == category.upper())

    corrections = query.order_by(Correction.page_number, Correction.category).all()

    # Corrections marquées comme faux positifs en DB (pour restaurer l'état FP au rechargement)
    fp_ids: set[str] = set(
        row[0]
        for row in db.query(CorrectionFeedback.correction_id)
        .filter(
            CorrectionFeedback.job_id == job_id,
            CorrectionFeedback.feedback_type == "false_positive",
        )
        .all()
    )

    return {
        "total": len(corrections),
        "corrections": [
            {
                "id": c.id,
                "page": c.page_number + 1,
                "category": c.category,
                "original_text": c.original_text,
                "corrected_text": c.corrected_text,
                "description": c.description,
                "explanation": c.explanation,
                "source": c.source,
                "annotation_type": c.annotation_type,
                "confidence": c.confidence or "Probable",
                "bbox": c.bbox,
                "is_user_added": bool(c.is_user_added),
                "pinned": bool(c.pinned),
                "liked": bool(c.liked),
                "is_false_positive": c.id in fp_ids,
            }
            for c in corrections
        ],
    }


@router.get("/jobs/{job_id}/debug")
def debug_job(job_id: str, db: Session = Depends(get_db)):
    """
    Debug endpoint: returns correction counts per category + samples.
    Only available when DEBUG_ENDPOINTS=true — never enable in production.
    """
    if not _DEBUG_ENABLED:
        raise HTTPException(status_code=404, detail="Not found.")

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable.")

    corrections = db.query(Correction).filter(Correction.job_id == job_id).all()
    by_cat: dict[str, list] = {}
    for c in corrections:
        by_cat.setdefault(c.category, []).append(c)

    summary = {}
    for cat, items in sorted(by_cat.items()):
        summary[cat] = {
            "count": len(items),
            "samples": [
                {
                    "page": i.page_number + 1,
                    "original": i.original_text[:80],
                    "corrected": i.corrected_text,
                    "description": i.description,
                    "confidence": i.confidence,
                    "source": i.source[:60] if i.source else None,
                }
                for i in items[:3]
            ],
        }

    return {
        "job_id": job_id,
        "status": job.status,
        "doc_type": job.doc_type,
        "total_corrections": len(corrections),
        "by_category": summary,
    }


@router.post("/jobs/{job_id}/reset")
def reset_job(job_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Reset a failed job back to awaiting_confirmation so the user can retry
    with the same or different preset/metadata.
    Deletes existing corrections so the pipeline starts fresh.
    """
    job = _get_job(job_id, current_user, db)
    if job.status != "error":
        raise HTTPException(status_code=409, detail="Seuls les jobs en erreur peuvent être réinitialisés.")
    # Delete existing (partial) corrections
    db.query(Correction).filter(Correction.job_id == job_id).delete()
    job.status = "awaiting_confirmation"
    job.progress = 0
    job.progress_label = "En attente de confirmation…"
    job.error_message = None
    job.corrections_count = 0
    job.corrections_by_category = {}
    db.commit()
    return JSONResponse({"job_id": job_id, "status": "awaiting_confirmation"})


def _safe_upload_path(raw_path: str) -> Path:
    """
    Resolve raw_path and verify it lives inside the UPLOAD_DIR.
    Raises HTTPException 404 if the path escapes the expected directory.
    """
    from backend.config import settings

    allowed_root = Path(settings.UPLOAD_DIR).resolve()
    resolved = Path(raw_path).resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError:
        logger.warning("Path traversal attempt blocked (upload): %s", raw_path)
        raise HTTPException(status_code=404, detail="Fichier PDF introuvable.")
    return resolved


def _safe_output_path(raw_path: str) -> Path:
    """
    Resolve raw_path and verify it lives inside the OUTPUT_DIR.
    Raises HTTPException 404 if the path escapes the expected directory.
    """
    from backend.config import settings

    allowed_root = Path(settings.OUTPUT_DIR).resolve()
    resolved = Path(raw_path).resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError:
        logger.warning("Path traversal attempt blocked: %s", raw_path)
        raise HTTPException(status_code=404, detail="Fichier de sortie introuvable.")
    return resolved


@router.get("/jobs/{job_id}/download")
def download_pdf(job_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Download the annotated PDF."""
    job = _get_job(job_id, current_user, db)

    if job.status != "done":
        raise HTTPException(status_code=409, detail="Le PDF n'est pas encore prêt.")

    if not job.output_pdf_path:
        raise HTTPException(status_code=404, detail="Fichier de sortie introuvable.")

    output_path = _safe_output_path(job.output_pdf_path)
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Fichier de sortie introuvable.")

    # Derive preset label from stored enabled_categories
    _PRESET_LABELS = {
        frozenset("ABCDEFGH"): "correction-complete",
        frozenset("ABCD"):     "correction-rapide",
        frozenset("EFG"):      "coherence-globale",
        frozenset("H"):        "verification-faits",
    }
    cats = frozenset(job.enabled_categories or list("ABCDEFGH"))
    preset_label = _PRESET_LABELS.get(cats, "correction")
    safe_stem = Path(job.filename).stem
    safe_output_filename = f"{safe_stem}_{preset_label}.pdf"
    return FileResponse(
        path=str(output_path),
        media_type="application/pdf",
        filename=safe_output_filename,
        headers={"Content-Disposition": f'attachment; filename="{safe_output_filename}"'},
    )


@router.get("/jobs/{job_id}/download-docx")
def download_docx(job_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Génère et télécharge un rapport Word (.docx) des corrections."""
    job = _get_job(job_id, current_user, db)
    if job.status != "done":
        raise HTTPException(status_code=409, detail="Le rapport n'est pas encore prêt.")

    corrections = (
        db.query(Correction)
        .filter(Correction.job_id == job_id)
        .order_by(Correction.page_number, Correction.category)
        .all()
    )

    # Sanitize stem — job.filename is already sanitised at upload time,
    # but be defensive in case of legacy records.
    safe_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
    stem = Path(job.filename).stem
    safe_stem = "".join(c if c in safe_chars else "_" for c in stem) or "rapport"
    output_filename = f"rapport_{safe_stem}.docx"
    tmp_path = str(Path(tempfile.mkdtemp()) / output_filename)

    try:
        export_corrections_docx(corrections, job, tmp_path)
    except Exception as exc:
        logger.error("Erreur génération DOCX — job %s : %s", job_id, exc)
        raise HTTPException(status_code=500, detail="Erreur lors de la génération du rapport.")

    return FileResponse(
        path=tmp_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=output_filename,
        headers={"Content-Disposition": f'attachment; filename="{output_filename}"'},
    )


@router.patch("/jobs/{job_id}/corrections/{correction_id}/pin")
def toggle_pin(job_id: str, correction_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Épingle / désépingle une correction (toggle persistant)."""
    _get_job(job_id, current_user, db)
    correction = (
        db.query(Correction)
        .filter(Correction.id == correction_id, Correction.job_id == job_id)
        .first()
    )
    if not correction:
        raise HTTPException(status_code=404, detail="Correction introuvable.")
    correction.pinned = not bool(correction.pinned)
    db.commit()
    return JSONResponse({"id": correction_id, "pinned": bool(correction.pinned)})


@router.patch("/jobs/{job_id}/corrections/{correction_id}/like")
def toggle_like(job_id: str, correction_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Marque / démarque une correction comme pertinente (toggle ❤️ persistant)."""
    _get_job(job_id, current_user, db)
    correction = (
        db.query(Correction)
        .filter(Correction.id == correction_id, Correction.job_id == job_id)
        .first()
    )
    if not correction:
        raise HTTPException(status_code=404, detail="Correction introuvable.")
    correction.liked = not bool(correction.liked)
    db.commit()
    return JSONResponse({"id": correction_id, "liked": bool(correction.liked)})


class RejectBody(BaseModel):
    reason_codes: list[str] = []   # nouvelle API : plusieurs raisons possibles
    reason_code: str | None = None  # rétrocompat : ancienne API (ignoré si reason_codes fourni)
    comment: str | None = None


@router.post("/jobs/{job_id}/corrections/{correction_id}/reject")
def reject_correction(
    job_id: str,
    correction_id: str,
    body: RejectBody = Body(default_factory=RejectBody),
    db: Session = Depends(get_db),
):
    """
    Signal éditeur : cette correction est un faux positif.

    Enregistre le feedback dans correction_feedback pour analyse.
    N'efface pas la correction de la DB (traçabilité) — le frontend gère l'état local.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable.")

    correction = (
        db.query(Correction)
        .filter(Correction.id == correction_id, Correction.job_id == job_id)
        .first()
    )
    if not correction:
        raise HTTPException(status_code=404, detail="Correction introuvable.")

    # Idempotent : ne pas créer de doublon si déjà signalé
    existing = (
        db.query(CorrectionFeedback)
        .filter(
            CorrectionFeedback.correction_id == correction_id,
            CorrectionFeedback.feedback_type == "false_positive",
        )
        .first()
    )
    if existing:
        return JSONResponse({"status": "already_reported", "feedback_id": existing.id})

    # Résoudre les raisons : nouvelle API (reason_codes) prioritaire sur ancienne (reason_code)
    effective_reasons = body.reason_codes if body.reason_codes else (
        [body.reason_code] if body.reason_code else []
    )
    # Pour rétrocompatibilité : stocker la première raison dans reason_code
    primary_reason = effective_reasons[0] if effective_reasons else None

    feedback = CorrectionFeedback(
        correction_id=correction_id,
        job_id=job_id,
        category=correction.category,
        original_text=correction.original_text,
        corrected_text=correction.corrected_text,
        description=correction.description,
        confidence=correction.confidence,
        doc_type=job.doc_type,
        feedback_type="false_positive",
        reason_code=primary_reason,
        comment=body.comment,
    )
    db.add(feedback)
    db.flush()  # génère feedback.id sans commit

    # Enregistrer toutes les raisons dans la table dédiée
    for code in effective_reasons:
        db.add(CorrectionFeedbackReason(
            feedback_id=feedback.id,
            reason_code=code,
        ))

    db.commit()
    db.refresh(feedback)

    logger.info(
        "Faux positif signalé — job=%s cat=%s reasons=%s orig=%s",
        job_id, correction.category,
        ",".join(effective_reasons) if effective_reasons else "unspecified",
        correction.original_text[:60],
    )

    return JSONResponse({"status": "reported", "feedback_id": feedback.id})


@router.delete("/jobs/{job_id}/corrections/{correction_id}/reject")
def unreject_correction(job_id: str, correction_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Annule le signalement de faux positif pour une correction (bouton « Rétablir »).
    Supprime les entrées CorrectionFeedback et CorrectionFeedbackReason associées.
    """
    feedbacks = (
        db.query(CorrectionFeedback)
        .filter(
            CorrectionFeedback.correction_id == correction_id,
            CorrectionFeedback.feedback_type == "false_positive",
        )
        .all()
    )
    if not feedbacks:
        raise HTTPException(status_code=404, detail="Aucun signalement trouvé pour cette correction.")

    for fb in feedbacks:
        db.query(CorrectionFeedbackReason).filter(
            CorrectionFeedbackReason.feedback_id == fb.id
        ).delete()
        db.delete(fb)

    db.commit()
    logger.info("Faux positif annulé — correction=%s", correction_id)
    return JSONResponse({"status": "restored"})


@router.post("/jobs/{job_id}/corrections/auto-reject-syllabic")
def auto_reject_syllabic(job_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Marque automatiquement toutes les corrections 'COUPURE SYLLABIQUE' du job comme FP.
    Appelé silencieusement par le frontend au chargement des corrections.
    Idempotent — ne crée pas de doublon.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable.")

    corrections = (
        db.query(Correction)
        .filter(Correction.job_id == job_id)
        .all()
    )

    syllabic = [
        c for c in corrections
        if c.description and "COUPURE SYLLABIQUE" in c.description.upper()
    ]

    rejected_ids: list[str] = []
    for correction in syllabic:
        existing = (
            db.query(CorrectionFeedback)
            .filter(
                CorrectionFeedback.correction_id == correction.id,
                CorrectionFeedback.feedback_type == "false_positive",
            )
            .first()
        )
        if existing:
            rejected_ids.append(correction.id)
            continue

        feedback = CorrectionFeedback(
            correction_id=correction.id,
            job_id=job_id,
            category=correction.category,
            original_text=correction.original_text,
            corrected_text=correction.corrected_text,
            description=correction.description,
            confidence=correction.confidence,
            doc_type=job.doc_type,
            feedback_type="false_positive",
            reason_code="syllabic_break_artifact",
            comment="Coupure syllabique de fin de ligne — artefact PDF automatiquement rejeté.",
        )
        db.add(feedback)
        rejected_ids.append(correction.id)

    db.commit()
    return {"rejected_ids": rejected_ids, "count": len(rejected_ids)}


class AddCorrectionBody(BaseModel):
    page_number: int       # 1-indexed from frontend
    category: str          # A–H
    original_text: str
    corrected_text: str | None = None
    description: str | None = None


_ANNOTATION_COLORS: dict[str, tuple[float, float, float]] = {
    "A": (0.8, 0.0, 0.0),
    "B": (0.9, 0.42, 0.0),
    "C": (0.4, 0.0, 0.8),
    "D": (0.0, 0.3, 0.8),
    "E": (0.0, 0.5, 0.1),
    "F": (0.0, 0.5, 0.6),
    "G": (0.8, 0.0, 0.4),
    "H": (0.72, 0.53, 0.0),
}


@router.post("/jobs/{job_id}/corrections/add")
def add_user_correction(
    job_id: str,
    body: AddCorrectionBody,
    db: Session = Depends(get_db),
):
    """
    Ajoute une correction signalée manuellement par l'éditeur.
    Stockée avec is_user_added=True et un badge distinct dans l'UI.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable.")
    if job.status != "done":
        raise HTTPException(status_code=409, detail="Le job n'est pas encore terminé.")

    cat = body.category.upper()
    if cat not in "ABCDEFGH" or len(cat) != 1:
        raise HTTPException(status_code=422, detail="Catégorie invalide (A–H).")

    if not body.original_text or not body.original_text.strip():
        raise HTTPException(status_code=422, detail="Le texte original est requis.")

    color = _ANNOTATION_COLORS.get(cat, (0.4, 0.4, 0.4))
    correction = Correction(
        job_id=job_id,
        page_number=max(0, body.page_number - 1),  # store 0-indexed
        category=cat,
        original_text=body.original_text.strip()[:300],
        corrected_text=(body.corrected_text or "").strip()[:300] or None,
        description=(body.description or "").strip()[:200] or None,
        confidence="Probable",
        annotation_type="Highlight",
        color_r=color[0],
        color_g=color[1],
        color_b=color[2],
        is_user_added=True,
    )
    db.add(correction)
    db.commit()
    db.refresh(correction)

    logger.info(
        "Correction éditeur ajoutée — job=%s cat=%s page=%d orig=%s",
        job_id, cat, body.page_number, body.original_text[:60],
    )
    return JSONResponse({"id": correction.id, "status": "added"})


@router.get("/feedback/stats")
def feedback_stats(db: Session = Depends(get_db), _: User = Depends(get_admin_user)):
    """
    Statistiques des faux positifs signalés par les éditeurs.
    Accessible en local pour analyse — permet d'identifier les patterns à corriger.
    """
    total = db.query(CorrectionFeedback).count()
    if total == 0:
        return {"total": 0, "by_category": {}, "by_doc_type": {}, "top_originals": []}

    # Répartition par catégorie
    by_cat_q = (
        db.query(CorrectionFeedback.category, func.count(CorrectionFeedback.id))
        .group_by(CorrectionFeedback.category)
        .all()
    )
    by_category = {cat: cnt for cat, cnt in by_cat_q}

    # Répartition par type de document
    by_doc_q = (
        db.query(CorrectionFeedback.doc_type, func.count(CorrectionFeedback.id))
        .group_by(CorrectionFeedback.doc_type)
        .all()
    )
    by_doc_type = {doc: cnt for doc, cnt in by_doc_q}

    # Top 20 textes originaux les plus fréquemment rejetés
    top_q = (
        db.query(
            CorrectionFeedback.original_text,
            CorrectionFeedback.category,
            CorrectionFeedback.description,
            func.count(CorrectionFeedback.id).label("count"),
        )
        .group_by(
            CorrectionFeedback.original_text,
            CorrectionFeedback.category,
            CorrectionFeedback.description,
        )
        .order_by(func.count(CorrectionFeedback.id).desc())
        .limit(20)
        .all()
    )
    top_originals = [
        {
            "original_text": row.original_text[:100],
            "category": row.category,
            "description": row.description,
            "count": row.count,
        }
        for row in top_q
    ]

    return {
        "total": total,
        "by_category": by_category,
        "by_doc_type": by_doc_type,
        "top_originals": top_originals,
    }


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Tableau de bord — statistiques globales et historique complet des analyses.
    Inclut le taux de faux positifs par raison, par catégorie, et par document.
    """
    # Admins see all jobs; regular users see only their own
    q = db.query(Job).order_by(Job.created_at.desc())
    if current_user.role != "admin":
        q = q.filter(Job.user_id == current_user.id)
    jobs = q.limit(200).all()

    # Compter les feedbacks par job
    fp_counts_q = (
        db.query(CorrectionFeedback.job_id, func.count(CorrectionFeedback.id))
        .group_by(CorrectionFeedback.job_id)
        .all()
    )
    fp_counts = {jid: cnt for jid, cnt in fp_counts_q}

    # Compter les corrections sans bbox (non localisées dans le PDF) par job
    unlocated_q = (
        db.query(
            Correction.job_id,
            func.count(Correction.id)
        )
        .filter(Correction.bbox.is_(None))
        .group_by(Correction.job_id)
        .all()
    )
    unlocated_counts = {jid: cnt for jid, cnt in unlocated_q}
    total_unlocated = sum(unlocated_counts.values())

    total_corrections = sum(j.corrections_count or 0 for j in jobs)
    total_fp = sum(fp_counts.values())

    # Répartition des faux positifs par raison
    by_reason_q = (
        db.query(CorrectionFeedback.reason_code, func.count(CorrectionFeedback.id))
        .group_by(CorrectionFeedback.reason_code)
        .all()
    )
    by_reason = {(code or "unspecified"): cnt for code, cnt in by_reason_q}

    # Répartition par catégorie
    by_cat_q = (
        db.query(CorrectionFeedback.category, func.count(CorrectionFeedback.id))
        .group_by(CorrectionFeedback.category)
        .all()
    )
    by_category = {cat: cnt for cat, cnt in by_cat_q}

    return {
        "total_jobs": len(jobs),
        "total_corrections": total_corrections,
        "total_false_positives": total_fp,
        "total_unlocated": total_unlocated,
        "by_reason": by_reason,
        "by_category": by_category,
        "jobs": [
            {
                "id": j.id,
                "filename": j.filename,
                "status": j.status,
                "corrections_count": j.corrections_count or 0,
                "false_positives_count": fp_counts.get(j.id, 0),
                "unlocated_count": unlocated_counts.get(j.id, 0),
                "doc_type": j.doc_type or "autre",
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "actual_cost_usd": j.actual_cost_usd,
            }
            for j in jobs
        ],
    }


@router.get("/dashboard/export")
def export_dashboard(
    format: str = Query(default="json", pattern="^(json|csv)$"),
    db: Session = Depends(get_db),
):
    """
    Export des feedbacks faux positifs en JSON ou CSV.
    Utile pour analyse hors-ligne et amélioration des prompts.
    """
    import csv
    import io
    import json as _json

    feedbacks = (
        db.query(CorrectionFeedback)
        .order_by(CorrectionFeedback.created_at.desc())
        .all()
    )

    rows = [
        {
            "id": f.id,
            "job_id": f.job_id,
            "category": f.category,
            "reason_code": f.reason_code or "",
            "original_text": f.original_text,
            "corrected_text": f.corrected_text or "",
            "description": f.description or "",
            "confidence": f.confidence or "",
            "doc_type": f.doc_type or "",
            "comment": f.comment or "",
            "created_at": f.created_at.isoformat() if f.created_at else "",
        }
        for f in feedbacks
    ]

    if format == "csv":
        output = io.StringIO()
        if rows:
            writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return Response(
            content=output.getvalue().encode("utf-8-sig"),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="faux_positifs_export.csv"'},
        )

    return Response(
        content=_json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="faux_positifs_export.json"'},
    )


@router.get("/jobs/{job_id}/pages/{page_num}/preview")
def preview_page(
    job_id: str,
    page_num: int,
    dpi: int = Query(default=130, ge=72, le=300),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Rend une page du PDF (annoté ou original) en PNG.
    Si le PDF annoté est disponible, il est utilisé avec ses annotations.
    Sinon (generate_pdf=False), repli sur le PDF original uploadé.
    dpi=130 → bonne lisibilité, ~150-200 Ko par page.
    """
    import fitz  # PyMuPDF

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable.")
    if job.status != "done":
        raise HTTPException(status_code=409, detail="PDF pas encore prêt.")

    # Use annotated PDF if available, else fall back to original uploaded PDF
    use_annotated = bool(job.output_pdf_path)
    if use_annotated:
        pdf_file = _safe_output_path(job.output_pdf_path)
    elif job.input_pdf_path:
        pdf_file = _safe_upload_path(job.input_pdf_path)
    else:
        raise HTTPException(status_code=404, detail="PDF introuvable.")

    if not pdf_file.exists():
        raise HTTPException(status_code=404, detail="Fichier PDF introuvable.")

    try:
        doc = fitz.open(str(pdf_file))
        total = len(doc)
        # Annotated PDF has a summary page at the end — skip it
        # Original PDF has no summary page
        if use_annotated:
            max_page = total - 2  # -1 for 0-index, -1 for summary page
        else:
            max_page = total - 1  # -1 for 0-index only
        p = max(0, min(page_num, max_page))
        page = doc[p]
        page_rect = page.rect
        width_pts = page_rect.width
        height_pts = page_rect.height
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        png_bytes = pix.tobytes("png")
        doc.close()
    except Exception as exc:
        logger.error("Erreur rendu page %d — job %s : %s", page_num, job_id, exc)
        raise HTTPException(status_code=500, detail="Erreur lors du rendu de la page.")

    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Page-Rendered": str(p),
            "X-Total-Pages": str(total),
            "X-Page-Width-Pts": str(width_pts),
            "X-Page-Height-Pts": str(height_pts),
            "Access-Control-Expose-Headers": (
                "X-Page-Rendered, X-Total-Pages, X-Page-Width-Pts, X-Page-Height-Pts"
            ),
        },
    )


@router.get("/compare")
def compare_jobs(
    job_a: str = Query(...),
    job_b: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Compare deux analyses du même document et retourne un rapport de stabilité.
    Utilise une correspondance floue (catégorie + texte normalisé) pour détecter
    les corrections communes malgré de légères différences de ponctuation/accents.
    """
    import unicodedata as _ud

    def _norm(text: str) -> str:
        nfkd = _ud.normalize("NFKD", (text or "").lower().strip())
        return "".join(c for c in nfkd if not _ud.combining(c))

    jobs: dict[str, Job] = {}
    corrections_map: dict[str, list[Correction]] = {}

    for jid in (job_a, job_b):
        job = db.query(Job).filter(Job.id == jid).first()
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {jid} introuvable.")
        jobs[jid] = job
        corrections_map[jid] = (
            db.query(Correction)
            .filter(Correction.job_id == jid)
            .order_by(Correction.page_number, Correction.category)
            .all()
        )

    def _key(c: Correction) -> tuple:
        return (c.category, _norm(c.original_text))

    keys_a = {_key(c): c for c in corrections_map[job_a]}
    keys_b = {_key(c): c for c in corrections_map[job_b]}
    all_keys = set(keys_a) | set(keys_b)

    common, only_a, only_b = [], [], []
    for key in all_keys:
        in_a, in_b = key in keys_a, key in keys_b
        if in_a and in_b:
            common.append(keys_a[key])
        elif in_a:
            only_a.append(keys_a[key])
        else:
            only_b.append(keys_b[key])

    total = len(all_keys)
    stability = round(len(common) / total, 3) if total > 0 else 1.0

    by_category: dict[str, dict] = {}
    for key, c in keys_a.items():
        cat = c.category
        by_category.setdefault(cat, {"common": 0, "only_a": 0, "only_b": 0})
        by_category[cat]["common" if key in keys_b else "only_a"] += 1
    for key, c in keys_b.items():
        if key not in keys_a:
            cat = c.category
            by_category.setdefault(cat, {"common": 0, "only_a": 0, "only_b": 0})
            by_category[cat]["only_b"] += 1

    def _fmt(c: Correction) -> dict:
        return {
            "id": c.id,
            "page": c.page_number + 1,
            "category": c.category,
            "original_text": c.original_text,
            "corrected_text": c.corrected_text,
            "description": c.description,
            "confidence": c.confidence or "Probable",
        }

    def _sort(lst: list[Correction]) -> list[dict]:
        return [_fmt(c) for c in sorted(lst, key=lambda c: (c.page_number, c.category))]

    same_file = (
        Path(jobs[job_a].filename).stem.lower() == Path(jobs[job_b].filename).stem.lower()
    )

    return {
        "job_a": {
            "id": jobs[job_a].id,
            "filename": jobs[job_a].filename,
            "created_at": jobs[job_a].created_at.isoformat() if jobs[job_a].created_at else None,
            "corrections_count": len(corrections_map[job_a]),
        },
        "job_b": {
            "id": jobs[job_b].id,
            "filename": jobs[job_b].filename,
            "created_at": jobs[job_b].created_at.isoformat() if jobs[job_b].created_at else None,
            "corrections_count": len(corrections_map[job_b]),
        },
        "common": _sort(common),
        "only_a": _sort(only_a),
        "only_b": _sort(only_b),
        "stability_score": stability,
        "total_unique": total,
        "by_category": by_category,
        "same_file": same_file,
    }


@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """List all jobs (most recent first)."""
    q = db.query(Job).order_by(Job.created_at.desc())
    if current_user.role != "admin":
        q = q.filter(Job.user_id == current_user.id)
    jobs = q.limit(50).all()
    return {
        "jobs": [
            {
                "id": j.id,
                "filename": j.filename,
                "status": j.status,
                "progress": j.progress,
                "corrections_count": j.corrections_count,
                "doc_type": j.doc_type,
                "created_at": j.created_at.isoformat() if j.created_at else None,
            }
            for j in jobs
        ]
    }
