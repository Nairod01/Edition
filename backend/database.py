from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from backend.config import settings
import logging

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate_db():
    """
    Migration légère SQLite : ajoute les colonnes manquantes sans toucher aux données existantes.
    Appelée automatiquement au démarrage.
    """
    try:
        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()

        with engine.connect() as conn:
            # ── Table corrections ──────────────────────────────────────────────
            if "corrections" in existing_tables:
                corr_cols = [col["name"] for col in inspector.get_columns("corrections")]
                if "confidence" not in corr_cols:
                    conn.execute(text(
                        "ALTER TABLE corrections ADD COLUMN confidence VARCHAR(20) DEFAULT 'Probable'"
                    ))
                    conn.commit()
                    logger.info("Migration : colonne 'confidence' ajoutée à corrections")
                if "is_user_added" not in corr_cols:
                    conn.execute(text(
                        "ALTER TABLE corrections ADD COLUMN is_user_added BOOLEAN DEFAULT 0"
                    ))
                    conn.commit()
                    logger.info("Migration : colonne 'is_user_added' ajoutée à corrections")
                if "pinned" not in corr_cols:
                    conn.execute(text(
                        "ALTER TABLE corrections ADD COLUMN pinned BOOLEAN DEFAULT 0"
                    ))
                    conn.commit()
                    logger.info("Migration : colonne 'pinned' ajoutée à corrections")
                if "liked" not in corr_cols:
                    conn.execute(text(
                        "ALTER TABLE corrections ADD COLUMN liked BOOLEAN DEFAULT 0"
                    ))
                    conn.commit()
                    logger.info("Migration : colonne 'liked' ajoutée à corrections")

            # ── Table jobs ─────────────────────────────────────────────────────
            if "jobs" in existing_tables:
                job_cols = [col["name"] for col in inspector.get_columns("jobs")]
                if "doc_type" not in job_cols:
                    conn.execute(text(
                        "ALTER TABLE jobs ADD COLUMN doc_type VARCHAR(30) DEFAULT 'autre'"
                    ))
                    conn.commit()
                    logger.info("Migration : colonne 'doc_type' ajoutée à jobs")
                # Map of column → definition; col_name is whitelisted, col_def is a literal
                # string constant — not user input — so interpolation is safe here.
                # Nonetheless, col_name is explicitly validated against _ALLOWED_COL_RE.
                import re as _re
                _ALLOWED_COL_RE = _re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
                new_job_cols = {
                    "enabled_categories": "TEXT DEFAULT '[\"A\",\"B\",\"C\",\"D\",\"E\",\"F\",\"G\",\"H\"]'",
                    "metadata_author": "VARCHAR(500)",
                    "metadata_title": "VARCHAR(500)",
                    "metadata_characters": "TEXT",
                    "metadata_citation_lang": "VARCHAR(100)",
                    "annotated_count": "INTEGER DEFAULT 0",
                    "h_not_annotated_count": "INTEGER DEFAULT 0",
                    "generate_pdf": "BOOLEAN DEFAULT 1",
                    "actual_cost_usd": "FLOAT",
                    "actual_tokens_input": "INTEGER",
                    "actual_tokens_output": "INTEGER",
                    "actual_tokens_cache_read": "INTEGER",
                }
                for col_name, col_def in new_job_cols.items():
                    if not _ALLOWED_COL_RE.match(col_name):
                        logger.error("Migration : nom de colonne invalide ignoré : %r", col_name)
                        continue
                    if col_name not in job_cols:
                        conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
                        logger.info("Migration : colonne '%s' ajoutée à jobs", col_name)

            # ── Table jobs — user_id ──────────────────────────────────────────
            if "jobs" in existing_tables:
                job_cols2 = [col["name"] for col in inspector.get_columns("jobs")]
                if "user_id" not in job_cols2:
                    conn.execute(text("ALTER TABLE jobs ADD COLUMN user_id VARCHAR"))
                    conn.commit()
                    logger.info("Migration : colonne 'user_id' ajoutée à jobs")

            # ── Table correction_feedback ──────────────────────────────────────
            if "correction_feedback" in existing_tables:
                fb_cols = [col["name"] for col in inspector.get_columns("correction_feedback")]
                if "reason_code" not in fb_cols:
                    conn.execute(text(
                        "ALTER TABLE correction_feedback ADD COLUMN reason_code VARCHAR(50)"
                    ))
                    conn.commit()
                    logger.info("Migration : colonne 'reason_code' ajoutée à correction_feedback")

    except Exception as exc:
        logger.warning("Migration DB : %s (non bloquant)", exc)


def init_db():
    from backend.models import Job, Correction, CorrectionFeedback, User  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _migrate_db()
