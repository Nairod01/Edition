import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, DateTime, Text, Boolean, JSON, ForeignKey
from sqlalchemy.sql import func
from backend.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=gen_uuid)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    name = Column(String(100), nullable=True)
    role = Column(String(20), default="user")      # "user" | "admin"
    is_active = Column(Boolean, default=True)

    # Credits (stored in USD — displayed in EUR in the UI)
    monthly_limit_usd = Column(Float, default=11.0)       # 0 = unlimited (admin)
    current_month_spend_usd = Column(Float, default=0.0)
    last_reset_at = Column(DateTime, default=datetime.utcnow)

    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=gen_uuid)
    filename = Column(String, nullable=False)

    # Status flow:
    # pending → extracting → awaiting_confirmation → processing → annotating → done | error
    status = Column(String, default="pending")
    progress = Column(Integer, default=0)
    progress_label = Column(String, default="En attente…")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    pages_count = Column(Integer, nullable=True)
    word_count = Column(Integer, nullable=True)
    estimated_tokens = Column(Integer, nullable=True)
    estimated_cost_usd = Column(Float, nullable=True)

    confirmed = Column(Boolean, default=False)

    # Owner — nullable for backward compat with pre-auth jobs
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # roman | manuel_scolaire | essai | autre
    doc_type = Column(String(30), default="autre")

    input_pdf_path = Column(String, nullable=True)
    output_pdf_path = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)

    corrections_count = Column(Integer, default=0)
    # Breakdown by category
    corrections_by_category = Column(JSON, default=dict)

    enabled_categories = Column(JSON, default=lambda: list("ABCDEFGH"))
    metadata_author = Column(String(500), nullable=True)
    metadata_title = Column(String(500), nullable=True)
    metadata_characters = Column(Text, nullable=True)
    metadata_citation_lang = Column(String(100), nullable=True)

    # Annotation stats — set after PDF annotation
    annotated_count = Column(Integer, default=0)        # corrections placed in PDF
    h_not_annotated_count = Column(Integer, default=0)  # H corrections in report only
    generate_pdf = Column(Boolean, default=True)        # False → skip annotation, DOCX only

    # Actual usage stats — populated after pipeline completes
    actual_cost_usd = Column(Float, nullable=True)
    actual_tokens_input = Column(Integer, nullable=True)
    actual_tokens_output = Column(Integer, nullable=True)
    actual_tokens_cache_read = Column(Integer, nullable=True)


class Correction(Base):
    __tablename__ = "corrections"

    id = Column(String, primary_key=True, default=gen_uuid)
    job_id = Column(String, nullable=False, index=True)

    page_number = Column(Integer, nullable=False)
    category = Column(String, nullable=False)  # A–H
    original_text = Column(Text, nullable=False)
    corrected_text = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    explanation = Column(Text, nullable=True)
    source = Column(Text, nullable=True)

    # Certain | Probable | À vérifier
    confidence = Column(String(20), default="Probable")

    annotation_type = Column(String, nullable=False)  # StrikeOut | Highlight | Squiggly
    color_r = Column(Float, nullable=False)
    color_g = Column(Float, nullable=False)
    color_b = Column(Float, nullable=False)

    # Bounding box in PDF coordinates (may be null if text not found)
    bbox = Column(JSON, nullable=True)  # {x0, y0, x1, y1}
    annotated = Column(Boolean, default=False)
    is_user_added = Column(Boolean, default=False)  # True = signalé par l'éditeur
    pinned = Column(Boolean, default=False)          # True = épinglé par l'éditeur
    liked = Column(Boolean, default=False)           # True = correction jugée pertinente (❤️)


class CorrectionFeedback(Base):
    """
    Feedback éditeur sur les corrections générées par l'IA.
    Chaque ligne correspond à un signal de rejet (faux positif signalé).

    Cette table est distincte de Correction pour ne pas nécessiter de migration :
    elle est créée automatiquement au démarrage si elle n'existe pas.

    Usage analytique :
    - Identifier les patterns de faux positifs par catégorie
    - Améliorer les prompts et les filtres
    - Construire un dataset pour le fine-tuning futur
    """
    __tablename__ = "correction_feedback"

    id = Column(String, primary_key=True, default=gen_uuid)
    correction_id = Column(String, nullable=False, index=True)  # FK logique vers Correction.id
    job_id = Column(String, nullable=False, index=True)

    # Snapshot de la correction au moment du feedback (pour analyse hors-ligne)
    category = Column(String(2), nullable=False)
    original_text = Column(Text, nullable=False)
    corrected_text = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    confidence = Column(String(20), nullable=True)
    doc_type = Column(String(30), nullable=True)

    # Signal éditeur
    feedback_type = Column(String(20), nullable=False, default="false_positive")
    # "false_positive" | "accepted" | "modified" — extensible
    reason_code = Column(String(50), nullable=True)
    # hallucination_text | already_correct | wrong_correction | wrong_fact_date |
    # passage_confusion | author_style | faithful_quote | fictional_term |
    # wrong_context | other | None (non précisé)
    comment = Column(Text, nullable=True)  # optionnel : raison du rejet (surtout pour "other")

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CorrectionFeedbackReason(Base):
    """
    Raisons multiples pour un même feedback faux-positif.
    Relation N:1 avec CorrectionFeedback.

    Permet à l'éditeur de sélectionner plusieurs raisons simultanément
    (ex : hallucination_text + wrong_context).
    """
    __tablename__ = "correction_feedback_reasons"

    id = Column(String, primary_key=True, default=gen_uuid)
    feedback_id = Column(String, nullable=False, index=True)  # FK logique → CorrectionFeedback.id
    reason_code = Column(String(50), nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
