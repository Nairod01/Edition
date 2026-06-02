"""
Pipeline principal — orchestre tous les services.

Architecture :
  0. Passe 0 : extraction métadonnées (Haiku, ~3 s) → personnages, conventions
  1. Extraction du texte
  2. Triple passe Claude : 1a (A/B/C/D, lots overlapping, parallèle)
                         + 1b (relecture A/B/C)
                         + 1c (zones grises — pages sans correction)
                         + 2  (E/F/G global)
  3. Fact-check : Claude + web_search natif Anthropic → catégorie H
     - Noms propres attestés dans le document exclus automatiquement
  4. Déduplication inter-catégories (floue + exacte)
  5. Annotation PDF
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import math
import re
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import SessionLocal
from backend.models import Correction, Job
from backend.services.claude_corrector import CATEGORY_CONFIG as CLAUDE_CONFIG
from backend.services.claude_corrector import MAX_CONCURRENT_BATCHES, correct_document
from backend.services.pdf_annotator import AnnotationRequest, annotate_pdf
from backend.services.pdf_extractor import ExtractionResult, extract
from backend.services.fact_checker import (
    CATEGORY_H_CONFIG,
    FactCheckItem,
    check_facts,
)

logger = logging.getLogger(__name__)

_CAT_PRIORITY = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8}


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _db_update(job_id: str, **kwargs):
    db: Session = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            for k, v in kwargs.items():
                setattr(job, k, v)
            db.commit()
    finally:
        db.close()


def _db_add_corrections(job_id: str, corrections: list[dict]):
    db: Session = SessionLocal()
    try:
        for c in corrections:
            db.add(Correction(job_id=job_id, **c))
        db.commit()
    finally:
        db.close()


def _db_get_corrections(job_id: str) -> list[Correction]:
    db: Session = SessionLocal()
    try:
        return db.query(Correction).filter(Correction.job_id == job_id).all()
    finally:
        db.close()


def _db_get_fp_patterns(doc_type: str) -> str:
    """
    Requête les patterns de faux positifs appris pour ce type de document.
    Renvoie une chaîne prête à être injectée dans le prompt système,
    ou chaîne vide si aucun pattern n'est disponible (≥2 signalements).
    """
    from backend.models import CorrectionFeedback
    from sqlalchemy import func as _func
    db: Session = SessionLocal()
    try:
        results = (
            db.query(
                CorrectionFeedback.category,
                CorrectionFeedback.reason_code,
                _func.count(CorrectionFeedback.id).label("count"),
            )
            .filter(
                CorrectionFeedback.doc_type == doc_type,
                CorrectionFeedback.feedback_type == "false_positive",
            )
            .group_by(CorrectionFeedback.category, CorrectionFeedback.reason_code)
            .having(_func.count(CorrectionFeedback.id) >= 2)
            .order_by(_func.count(CorrectionFeedback.id).desc())
            .limit(10)
            .all()
        )
        if not results:
            return ""
        _REASON_LABELS: dict[str, str] = {
            "hallucination_text": "passage inventé par l'IA",
            "already_correct": "texte déjà correct",
            "wrong_correction": "correction erronée proposée",
            "wrong_fact_date": "date ou fait mal évalué",
            "passage_confusion": "confusion entre passages",
            "author_style": "style voulu par l'auteur",
            "faithful_quote": "citation fidèle modifiée",
            "fictional_term": "terme fictif/inventé traité comme faute",
            "wrong_context": "mauvais contexte narratif",
        }
        lines = [
            "**PATTERNS DE FAUX POSITIFS DÉTECTÉS (apprentissage éditeur) :**",
            "L'éditeur a signalé ces types de faux positifs récurrents — évitez-les activement :",
        ]
        for row in results:
            cat = row.category or "?"
            reason = row.reason_code or "non précisé"
            label = _REASON_LABELS.get(reason, reason)
            n = row.count
            lines.append(
                f"- Catégorie {cat} : {label} ({n} signalement{'s' if n > 1 else ''})"
            )
        return "\n".join(lines)
    except Exception:
        logger.debug("_db_get_fp_patterns : erreur silencieuse")
        return ""
    finally:
        db.close()


def _resolve_correction_bboxes(job_id: str, pdf_path: str) -> None:
    """
    Recherche les coordonnées PDF (bbox) de chaque correction dans le document original.
    Stockées pour l'overlay animé dans l'UI.

    Stratégie multi-pass :
    1. Recherche robuste sur la page exacte via _find_text_rects (gère multi-lignes)
    2. Fallback fenêtre ±FALLBACK_WINDOW pages si introuvable sur la page déclarée
    3. Fallback document entier en dernier recours
    """
    from backend.services.pdf_annotator import _find_text_rects as _pa_find_text_rects
    try:
        import fitz
        db: Session = SessionLocal()
        try:
            corrections = (
                db.query(Correction)
                .filter(Correction.job_id == job_id, Correction.bbox.is_(None))
                .all()
            )
            if not corrections:
                return
            doc = fitz.open(pdf_path)
            total_pages = len(doc)
            updated = 0
            FALLBACK_WINDOW = 4

            for c in corrections:
                page_num = c.page_number
                if page_num >= total_pages:
                    continue

                # Retirer les marqueurs italique avant la recherche
                search_text = re.sub(r'\*([^*]+)\*', r'\1', c.original_text)

                # ── Essai 1 : page exacte avec stratégie robuste (multi-lignes) ──
                rects = _pa_find_text_rects(doc[page_num], search_text)
                found_page = page_num

                # ── Essai 2 : fenêtre étroite ±FALLBACK_WINDOW pages ──
                if not rects:
                    window_start = max(0, page_num - FALLBACK_WINDOW)
                    window_end   = min(total_pages, page_num + FALLBACK_WINDOW + 1)
                    for fb_idx in range(window_start, window_end):
                        if fb_idx == page_num:
                            continue
                        rects = _pa_find_text_rects(doc[fb_idx], search_text)
                        if rects:
                            found_page = fb_idx
                            break

                # ── Essai 3 : document entier ──
                if not rects:
                    for fb_idx in range(total_pages):
                        if window_start <= fb_idx < window_end:
                            continue  # déjà testé
                        rects = _pa_find_text_rects(doc[fb_idx], search_text)
                        if rects:
                            found_page = fb_idx
                            break

                if rects:
                    r = rects[0]
                    c.bbox = {"x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1}
                    if found_page != page_num:
                        logger.debug(
                            "Bbox fallback p.%d→p.%d [%s]: %s",
                            page_num + 1, found_page + 1, c.category, search_text[:50],
                        )
                    updated += 1

            db.commit()
            doc.close()
            logger.info("Bbox résolus : %d/%d corrections", updated, len(corrections))
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Résolution bbox : %s (non bloquant)", exc)


# ── Cache par hash de section ──────────────────────────────────────────────────

def _section_hash(text: str, doc_type: str) -> str:
    """Hash stable d'une section de texte + type de document."""
    return hashlib.sha256(f"{doc_type}:{text}".encode()).hexdigest()[:16]


# ── Cohérence interne des noms propres ────────────────────────────────────────

def _build_proper_noun_variants(extraction: ExtractionResult) -> dict[str, list[str]]:
    full_text = extraction.full_text
    variants_map: dict[str, set[str]] = defaultdict(set)
    for noun in extraction.proper_nouns:
        canonical = noun.strip()
        if not canonical or len(canonical.split()) < 2:
            continue
        variants_map[canonical].add(canonical)
        first_word = canonical.split()[0]
        pattern = re.compile(
            rf"\b{re.escape(first_word)}\s+[A-ZÀ-Ü][a-zà-ü\-]{{1,}}"
            rf"(?:\s+[A-ZÀ-Ü][a-zà-ü\-]{{1,}}){{0,2}}\b"
        )
        for m in pattern.finditer(full_text):
            found = m.group(0).strip()
            if found != canonical and len(found.split()) >= 2:
                variants_map[canonical].add(found)
    return {k: sorted(v) for k, v in variants_map.items()}


def _build_excluded_names(
    extraction: ExtractionResult,
    proper_noun_variants: dict[str, list[str]],
) -> set[str]:
    """
    Noms propres attestés ≥2 fois de façon cohérente dans le document.
    Ces noms ne sont jamais envoyés au fact-checker — le document fait autorité.
    """
    full_text = extraction.full_text
    excluded: set[str] = set()

    for name, variants in proper_noun_variants.items():
        count = full_text.count(name)
        if count >= 3 and len(variants) == 1:
            excluded.add(name)
        for v in variants:
            if full_text.count(v) >= 2:
                excluded.add(v)

    logger.info(
        "Noms propres attestés (exclus du fact-check) : %d — ex: %s",
        len(excluded),
        list(excluded)[:5],
    )
    return excluded


# ── Conversion fact_check_items → FactCheckItem ───────────────────────────────

def _build_fact_items(fact_dicts: list[dict], max_items: int = 50) -> list[FactCheckItem]:
    items: list[FactCheckItem] = []
    seen: set[str] = set()
    for fd in fact_dicts[:max_items]:
        text = (fd.get("text") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        item_type = fd.get("item_type", "proper_noun")
        if item_type not in ("date", "proper_noun", "title"):
            item_type = "proper_noun"
        page_hint = int(fd.get("page_hint", 1))
        items.append(FactCheckItem(
            query=text,
            context=(fd.get("context") or text)[:300],
            page_num=max(0, page_hint - 1),
            original_text=text,
            item_type=item_type,
        ))
    logger.info("Fact-check : %d éléments transmis par Claude", len(items))
    return items


# ── Dates directement depuis extraction ───────────────────────────────────────

def _build_date_items_from_extraction(extraction: ExtractionResult, max_dates: int = 20) -> list[FactCheckItem]:
    """
    Crée des FactCheckItems pour TOUTES les dates extraites du PDF.
    Contourne le filtre de Claude pour garantir une vérification exhaustive des dates.
    """
    items: list[FactCheckItem] = []
    seen: set[str] = set()
    for d in extraction.dates[:max_dates]:
        text = (d.get("text") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(FactCheckItem(
            query=text,
            context=(d.get("context") or text)[:300],
            page_num=int(d.get("page", 0)),
            original_text=text,
            item_type="date",
        ))
    logger.info("Dates extraites directement depuis le PDF : %d éléments", len(items))
    return items


# ── Déduplication ──────────────────────────────────────────────────────────────

def _texts_overlap(a: str, b: str) -> bool:
    """Retourne True si le texte le plus court est contenu dans le plus long (≥5 chars)."""
    if not a or not b:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) < 5:
        return False
    return shorter in longer


def _filter_false_positives(corrections: list[dict], full_text: str = "") -> list[dict]:
    """
    Retire les corrections qui ne sont pas de vraies erreurs.

    1. L'explication indique explicitement qu'il n'y a rien à corriger.
    2. Original == Correction (aucune vraie modification proposée).
    3. Guillemets fantômes : C + "guillemet" mais original contient déjà « ».
    4. Apostrophe fantôme : C + "apostrophe" mais original sans apostrophe ASCII.
    5. Chiffre de renvoi (exposant) : seule différence = un exposant final.
    6. Texte original trop court (< 3 caractères) — trop ambigu.
    7. H avec confidence Certain ou Probable — H est toujours "À vérifier"
       (protection redondante : fact_checker force déjà "À vérifier").
    """
    # Formulations indiquant l'absence d'erreur.
    _NO_ERROR_PHRASES = [
        "aucune erreur",
        "aucune faute",
        "pas d'erreur",
        "aucune correction nécessaire",
        "aucune modification nécessaire",
        "aucune anomalie",
        "l'accord est correct",
        "est grammaticalement correct",
        "c'est exact. aucune",
        "c'est correct. aucune",
        "tout est correct",
        "aucun problème détecté",
        "ne nécessite pas de correction",
        "aucune correction à apporter",
    ]

    # Chiffres en exposant (appels de notes)
    _SUPERSCRIPT = set("⁰¹²³⁴⁵⁶⁷⁸⁹")

    def _strip_superscripts(text: str) -> str:
        return text.rstrip("".join(_SUPERSCRIPT)).strip()

    # Pré-calcul : le document utilise-t-il massivement des guillemets français ?
    # Si oui, les corrections "ajouter des «»" sont des artefacts d'extraction PyMuPDF.
    doc_uses_french_guillemets = full_text.count('\u00ab') > 3

    filtered: list[dict] = []
    removed = 0
    removed_by_rule: dict[str, int] = {}

    def _reject(c: dict, rule: str) -> None:
        nonlocal removed
        cat = c.get("category", "?")
        orig = (c.get("original_text") or "")[:60]
        logger.debug("Faux positif (%s) rejeté : [%s] %s", rule, cat, orig)
        removed_by_rule[rule] = removed_by_rule.get(rule, 0) + 1
        removed += 1

    for c in corrections:
        expl = (c.get("explanation") or "").lower().strip()
        orig = (c.get("original_text") or "").strip()
        corr = (c.get("corrected_text") or "").strip()
        cat  = c.get("category", "")
        desc = (c.get("description") or "").lower()
        conf = c.get("confidence", "")

        # 1. Explication dit qu'il n'y a pas d'erreur
        if any(phrase in expl for phrase in _NO_ERROR_PHRASES):
            _reject(c, "explication_negative")
            continue

        # 2. Original == Correction
        if orig and corr and orig.lower() == corr.lower():
            _reject(c, "original_eq_correction")
            continue

        # 3. Guillemets fantômes
        if cat == "C" and "guillemet" in desc:
            if "\u00ab" in orig or "\u00bb" in orig:
                _reject(c, "guillemets_français_présents")
                continue
            # Artefact d'extraction PyMuPDF : le doc utilise les guillemets français («»)
            # mais fitz les a transcrits en guillemets droits ("") dans le texte extrait.
            # Si le contenu intérieur de l'original et de la correction est identique
            # (seule la forme du guillemet diffère) et que le doc utilise bien «» → rejet.
            if doc_uses_french_guillemets and corr and ("\u00ab" in corr or "\u00bb" in corr):
                _strip_q = re.compile(r'[«»"\u201c\u201d\u2018\u2019\s]')
                orig_inner = _strip_q.sub('', orig)
                corr_inner = _strip_q.sub('', corr)
                if orig_inner and orig_inner == corr_inner:
                    _reject(c, "guillemets_extraction_artefact")
                    continue

        # 4. Apostrophe fantôme
        if cat == "C" and "apostrophe" in desc:
            if "'" not in orig:
                _reject(c, "apostrophe_absente")
                continue

        # 5. Chiffre de renvoi (exposant)
        if orig and corr:
            if _strip_superscripts(orig) == corr or _strip_superscripts(orig) == _strip_superscripts(corr):
                _reject(c, "chiffre_renvoi")
                continue

        # 6. Texte original trop court — trop ambigu pour être actionnable
        if len(orig) < 3:
            _reject(c, "texte_trop_court")
            continue

        # 7. H ne peut être que "À vérifier" — protection redondante
        if cat == "H" and conf in ("Certain", "Probable"):
            c = {**c, "confidence": "À vérifier"}  # correction silencieuse, pas rejet

        # 8. Siècle en chiffres romains (XVIIe, XIXe…) — le "e" est un exposant aplati à l'extraction
        if cat in ("A", "C") and re.match(r'^[IVXLCDM]{2,}e(?:r|re|s)?$', orig, re.IGNORECASE):
            _reject(c, "siecle_romain")
            continue

        # 9. Appel de note extrait comme chiffre ordinaire : « 68¹ » → « 681 »
        #    Détecte orig numérique se terminant par 1-2 chiffres qui ne sont pas dans corr.
        #    Ex : orig="681", corr="68" → remove "1" (footnote ¹ extracted as 1).
        if corr.isdigit() and re.match(rf'^{re.escape(corr)}\d{{1,2}}$', orig):
            _reject(c, "chiffre_renvoi_extrait")
            continue

        # 9b. Appel de note collé à un mot : « auteur2 → auteur », « gauches1 → gauches »
        #     L'extraction PDF aplatit les superscripts Unicode (¹²³) en chiffres ASCII.
        #     Si orig = corr + 1 ou 2 chiffres ASCII en fin, c'est un renvoi de note, pas une faute.
        #     Garde-fous : orig ne doit pas être entièrement numérique (déjà géré R9),
        #     et le radical (corr) doit faire au moins 2 caractères pour éviter les faux rejets.
        if corr and orig and not orig.isdigit() and len(corr) >= 2:
            stripped = orig.rstrip("0123456789")
            trailing_digits = len(orig) - len(stripped)
            if 1 <= trailing_digits <= 2 and stripped.lower() == corr.lower():
                _reject(c, "chiffre_renvoi_mot_extrait")
                continue

        # 10. Coupure syllabique avec substitution lexicale erronée (C uniquement).
        #     Quand orig contient un tiret de coupure (ex : « piteu-sement »), la seule
        #     correction valide est la réunion sans tiret (« piteusement »).
        #     Si Claude propose un mot différent (ex : « pieusement »), c'est un faux positif.
        if cat == "C" and "-" in orig and corr:
            joined = orig.replace("-", "")
            # Accepter uniquement : corr == parties soudées (ex: piteusement)
            #                   ou : corr == orig (correction identique — déjà rejeté en règle 2)
            if corr.lower() != joined.lower() and corr.lower() != orig.lower():
                _reject(c, "coupure_substitution_erronee")
                continue

        # 11. Coupure syllabique déclarée — artefact systématique du PDF, jamais une faute éditoriale.
        #     La description produite par Claude contient explicitement "COUPURE SYLLABIQUE".
        if "COUPURE SYLLABIQUE" in (c.get("description") or "").upper():
            _reject(c, "coupure_syllabique_artefact")
            continue

        # 12. Ordinal numérique aplati à l'extraction : « 17e → 17ᵉ », « 1er → 1ᵉʳ »…
        #     L'extraction PDF aplatit les exposants typographiques (ᵉ, ᵉʳ) en lettres ASCII.
        #     Si orig = \d+ + suffixe ordinal ASCII et corr = même nombre + exposant Unicode, rejet.
        if cat in ("A", "C") and orig and corr:
            _ordinal_orig = re.match(r'^\d+(?:e|er|eme|ème|ere|ère|s|rs)$', orig, re.IGNORECASE)
            _ordinal_corr = re.match(r'^\d+\s*(?:ᵉ|ᵉʳ|ᵉʳˢ|ᵉʳᵉ|ʳᵉ|ʳ|ème|ère|[ᵒᵃ])', corr)
            if _ordinal_orig and _ordinal_corr:
                _reject(c, "ordinal_expose_extraction")
                continue

        # 13. Phrase tronquée inventée : corrected contient "[texte manquant]".
        #     Presque toujours un artefact de segmentation par pages — la suite du texte est
        #     dans le segment / la page suivante, pas réellement absente du document.
        if corr and "[texte manquant]" in corr.lower():
            _reject(c, "phrase_tronquee_artefact")
            continue

        # 14. Placeholder signalement préventif : Claude signale l'absence de placeholder
        #     même quand aucun n'est présent dans l'original (correction inventée).
        if cat == "G":
            _expl_desc = (expl + " " + desc).lower()
            if "signalement préventif" in _expl_desc:
                _reject(c, "placeholder_preventif")
                continue
            if "aucun placeholder" in _expl_desc and "compléter" in corr.lower():
                _reject(c, "placeholder_preventif")
                continue

        # 16. Saut de ligne dans le flux du texte — artefact de justification PDF.
        #     PyMuPDF insère des \n au sein de phrases continues (texte justifié).
        #     Ces corrections sont SYSTÉMATIQUEMENT des faux positifs.
        _desc_expl = desc + " " + expl
        if "saut de ligne" in _desc_expl or "retour à la ligne" in _desc_expl:
            _reject(c, "saut_de_ligne_artefact")
            continue

        # 17. Interligne / espacement entre paragraphes — mise en page, non applicable
        #     au texte (ÉditorIA traite le texte, pas la mise en page PDF).
        if cat == "F" and any(kw in _desc_expl for kw in (
            "interligne", "espacement entre paragraphe", "ligne vide",
            "espace entre paragraphe", "blanc entre", "saut de paragraphe",
        )):
            _reject(c, "interligne_mise_en_page")
            continue

        # 15. Double espace — artefact de justification PDF (maintenant normalisé à l'extraction,
        #     mais cette règle couvre les corrections déjà enregistrées en DB ou les cas résiduels).
        if cat == "C":
            _desc_lower = desc.lower()
            _expl_lower = expl.lower()
            if "double espace" in _desc_lower or "double espace" in _expl_lower:
                _reject(c, "double_espace_justification")
                continue
            # Variantes : deux espaces consécutifs dans l'original
            if orig and "  " in orig and corr and "  " not in corr:
                _reject(c, "double_espace_justification")
                continue

        filtered.append(c)

    if removed > 0:
        logger.info(
            "Faux positifs éliminés : %d/%d | par règle : %s",
            removed, len(corrections), removed_by_rule,
        )
    return filtered


def _norm_text(text: str) -> str:
    """Normalise pour la déduplication floue : bas de casse, supprime diacritiques et ponctuation."""
    nfkd = unicodedata.normalize('NFKD', text.lower().strip())
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


def _deduplicate(corrections: list[dict]) -> list[dict]:
    """
    Supprime les doublons :
    - Passe 1a : clé normalisée (page, texte sans diacritiques) — floue, résiste aux variantes d'accent.
    - Passe 1b : clé exacte (page, texte) pour les cas restants — catégorie la plus prioritaire.
    - Passe 2  : chevauchement de texte (un extrait en contient un autre) sur la même page.
    - Passe 3  : plafond 3 occurrences par (catégorie, texte) pour les catégories A/B/C.
    """
    # Passe 1 — clé normalisée (floue) : élimine les doublons qui diffèrent seulement par des accents
    seen_norm: dict[tuple[int, str], dict] = {}
    for c in corrections:
        key = (c["page_number"], _norm_text(c["original_text"]))
        if key not in seen_norm:
            seen_norm[key] = c
        else:
            if _CAT_PRIORITY.get(c["category"], 99) < _CAT_PRIORITY.get(seen_norm[key]["category"], 99):
                seen_norm[key] = c
    corrections = list(seen_norm.values())

    # Passe 1b — clé exacte (redondant si les corrections venaient d'overlap batches)
    seen: dict[tuple[int, str], dict] = {}
    for c in corrections:
        key = (c["page_number"], c["original_text"].lower().strip())
        if key not in seen:
            seen[key] = c
        else:
            if _CAT_PRIORITY.get(c["category"], 99) < _CAT_PRIORITY.get(seen[key]["category"], 99):
                seen[key] = c

    deduplicated = list(seen.values())

    # Passe 2 — chevauchement (substring) sur la même page
    sorted_corr = sorted(deduplicated, key=lambda x: _CAT_PRIORITY.get(x["category"], 99))
    final: list[dict] = []
    for c in sorted_corr:
        orig_c = c["original_text"].lower().strip()
        dominated = any(
            kept["page_number"] == c["page_number"]
            and _texts_overlap(orig_c, kept["original_text"].lower().strip())
            for kept in final
        )
        if not dominated:
            final.append(c)

    # Passe 3 — même erreur répétée sur de nombreuses pages (ex : typo dans un élément
    # récurrent du gabarit). Plafond à 3 occurrences par (catégorie, texte original).
    # Cats D–H gardées toutes (contextuelles, moins répétitives).
    _CAP_CATS = {"A", "B", "C"}
    _CAP_MAX = 3
    text_cat_count: dict[tuple[str, str], int] = {}
    capped: list[dict] = []
    for c in final:
        if c["category"] in _CAP_CATS:
            key = (c["category"], c["original_text"].lower().strip())
            text_cat_count[key] = text_cat_count.get(key, 0) + 1
            if text_cat_count[key] <= _CAP_MAX:
                capped.append(c)
        else:
            capped.append(c)
    final = capped

    removed = len(corrections) - len(final)
    if removed > 0:
        logger.info("Déduplication : %d doublon(s) supprimé(s)", removed)
    return final


# ── Estimation du temps restant ────────────────────────────────────────────────

def _format_remaining(seconds: float) -> str:
    if seconds < 5:   return "presque terminé…"
    if seconds < 60:  return f"~{int(seconds)}s"
    return f"~{int(seconds / 60)} min"


class TimeEstimator:
    BASE_PAGES = 50
    STEP_DURATIONS = {
        "extract":    3,
        "claude":   120,   # parallélisme → beaucoup plus rapide qu'avant
        "factcheck":  30,
        "annotate":    8,
    }

    def __init__(self, total_pages: int):
        ratio = max(1, total_pages) / self.BASE_PAGES
        self.estimated = {k: v * ratio for k, v in self.STEP_DURATIONS.items()}
        # For long docs, Pass 2 runs sequentially in segments — add conservative overhead
        if total_pages > 100:
            extra_segments = max(0, (total_pages - 100) // 60)
            self.estimated["claude"] += extra_segments * 25
        self.step_start: dict[str, float] = {}

    def start(self, step: str):
        self.step_start[step] = time.monotonic()

    def done(self, step: str):
        if step in self.step_start:
            self.estimated[step] = time.monotonic() - self.step_start[step]

    def remaining_after(self, current_step: str, pct_in_step: float = 0.0) -> float:
        steps = list(self.STEP_DURATIONS.keys())
        idx = steps.index(current_step) if current_step in steps else 0
        remaining = 0.0
        for i, step in enumerate(steps):
            est = self.estimated[step]
            if i < idx:
                continue
            elif i == idx:
                remaining += est * (1.0 - pct_in_step)
            else:
                remaining += est
        return remaining

    def label(self, step: str, step_label: str, pct: float = 0.0) -> str:
        return f"{step_label} — temps restant : {_format_remaining(self.remaining_after(step, pct))}"


# ── Pipeline principal ─────────────────────────────────────────────────────────

def _find_page_for_text(extraction: ExtractionResult, text: str) -> int:
    """Trouve la première page contenant ce texte dans l'extraction (0-indexed)."""
    for page in extraction.pages:
        if text in page.text:
            return page.page_num
    return 0


def _build_all_fact_items_from_extraction(
    extraction: ExtractionResult, max_items: int = 40
) -> list[FactCheckItem]:
    """Mode H-seul : construit les items depuis l'extraction sans passe Claude."""
    items: list[FactCheckItem] = []
    seen: set[str] = set()
    for d in extraction.dates[:20]:
        text = (d.get("text") or "").strip()
        if text and text not in seen:
            seen.add(text)
            items.append(FactCheckItem(
                query=text, context=(d.get("context") or text)[:300],
                page_num=int(d.get("page", 0)), original_text=text, item_type="date",
            ))
    for name in extraction.proper_nouns[:15]:
        if name and name not in seen:
            seen.add(name)
            # Cherche la page réelle au lieu d'utiliser 0 par défaut
            page_num = _find_page_for_text(extraction, name)
            items.append(FactCheckItem(
                query=name, context=name, page_num=page_num, original_text=name, item_type="proper_noun",
            ))
    for t in extraction.titles[:10]:
        text = (t.get("text") or "").strip()
        if text and text not in seen:
            seen.add(text)
            items.append(FactCheckItem(
                query=text, context=text,
                page_num=int(t.get("page", 0)), original_text=text, item_type="title",
            ))
    logger.info("Mode H-seul : %d éléments à vérifier depuis extraction", len(items))
    return items[:max_items]


def _update_user_credit(user_id: str | None, cost_usd: float) -> None:
    """Update user's monthly spend after pipeline completion (runs in background task)."""
    if not user_id or not cost_usd or cost_usd <= 0:
        return
    from backend.models import User
    from backend.database import SessionLocal
    from datetime import datetime as _dt
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            now = _dt.utcnow()
            reset = user.last_reset_at or now
            if reset.year != now.year or reset.month != now.month:
                user.current_month_spend_usd = 0.0
                user.last_reset_at = now
            user.current_month_spend_usd = round((user.current_month_spend_usd or 0.0) + cost_usd, 5)
            db.commit()
            logger.info("Crédits mis à jour — user=%s +%.4f$ = %.4f$ ce mois",
                        user_id, cost_usd, user.current_month_spend_usd)
    except Exception as exc:
        logger.warning("Impossible de mettre à jour les crédits — user=%s : %s", user_id, exc)
    finally:
        db.close()


async def run_pipeline(
    job_id: str,
    pdf_path: str,
    doc_type: str = "autre",
    enabled_categories: list[str] | None = None,
    metadata: dict | None = None,
    comment_mode: str = "detailed",
    generate_pdf: bool = True,
    user_id: str | None = None,
):
    logger.info("Pipeline démarré — job %s | doc_type=%s", job_id, doc_type)
    enabled_cats = set(enabled_categories) if enabled_categories else set("ABCDEFGH")
    # FP patterns : chargés pour le post-traitement uniquement — NE PAS injecter dans le
    # prompt Claude. L'injection changeait le prompt entre chaque run (quand l'éditeur
    # donnait un feedback), rendant les résultats non reproductibles (température=0 mais
    # prompt différent → output différent). _filter_false_positives() couvre déjà les cas
    # les plus courants (already_correct, syllabic_break, etc.) en post-traitement.
    metadata_dict = dict(metadata) if metadata else {}
    PASS1_NEEDED = bool(enabled_cats & {"A", "B", "C", "D"})
    PASS2_NEEDED = bool(enabled_cats & {"E", "F", "G"})
    FACTCHECK_NEEDED = "H" in enabled_cats
    logger.info(
        "Catégories activées : %s | P1=%s P2=%s H=%s | fp_patterns=post-traitement uniquement (reproductibilité)",
        enabled_cats, PASS1_NEEDED, PASS2_NEEDED, FACTCHECK_NEEDED,
    )

    try:
        # ── 1. Extraction ──────────────────────────────────────────────────────
        _db_update(job_id, status="extracting", progress=5,
                   progress_label="Extraction du texte en cours…")

        # Pour les livres d'art, filtrer le texte décoratif en très grande police
        # (filigranes, noms d'artistes en fond de page) avant envoi à Claude.
        extraction: ExtractionResult = extract(
            pdf_path,
            filter_large_font=(doc_type == "beaux_arts"),
        )
        timer = TimeEstimator(extraction.total_pages)

        _db_update(
            job_id,
            pages_count=extraction.total_pages,
            word_count=extraction.total_words,
            progress=10,
            progress_label=(
                f"Texte extrait — {extraction.total_pages} pages, "
                f"{extraction.total_words:,} mots"
            ),
        )

        proper_noun_variants = _build_proper_noun_variants(extraction)
        excluded_names = _build_excluded_names(extraction, proper_noun_variants)

        logger.info(
            "%d noms propres | %d avec variantes | %d exclus du fact-check",
            len(extraction.proper_nouns),
            sum(1 for v in proper_noun_variants.values() if len(v) > 1),
            len(excluded_names),
        )

        # ── 1bis. Passe 0 : extraction métadonnées document ───────────────────
        if PASS1_NEEDED or PASS2_NEEDED:
            from backend.services.claude_corrector import extract_doc_metadata
            logger.info("Passe 0 — extraction métadonnées du document…")
            _db_update(
                job_id, progress=12,
                progress_label="Analyse de la structure du document…",
            )
            try:
                metadata_dict = await asyncio.wait_for(
                    extract_doc_metadata(
                        extraction.pages,
                        doc_type=doc_type,
                        existing_metadata=metadata_dict,
                    ),
                    timeout=30.0,
                )
                logger.info(
                    "Passe 0 terminée — métadonnées : %s",
                    {k: v for k, v in metadata_dict.items() if v},
                )
            except asyncio.TimeoutError:
                logger.warning("Passe 0 timeout (>30s) — métadonnées ignorées, pipeline continue")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Passe 0 erreur (%s) — pipeline continue sans métadonnées", exc)

        # ── 2. Claude (triple passe, lots parallèles) ─────────────────────────
        timer.start("claude")

        batches_done = {"count": 0}

        async def claude_progress(batch_idx: int, total_batches: int):
            batches_done["count"] += 1
            done = batches_done["count"]
            if batch_idx == -1:
                # Signal Passe 1c (zones grises)
                pct_in_step = 0.92
                label = timer.label("claude", "Analyse — Passe 1c : vérification des pages sans corrections…", pct_in_step)
            elif batch_idx >= total_batches:
                # Signal Passe 1b (relecture)
                pct_in_step = 0.80
                label = timer.label("claude", "Analyse — Passe 1b : relecture A/B/C…", pct_in_step)
            else:
                pct_in_step = min(done / max(total_batches, 1), 1.0) * 0.65
                label = timer.label(
                    "claude",
                    f"Analyse — Passe 1a : {done}/{total_batches} lots traités",
                    pct_in_step,
                )
            pct = 12 + int(pct_in_step * 66)
            _db_update(job_id, progress=pct, progress_label=label)

        _db_update(
            job_id, status="processing", progress=12,
            progress_label=timer.label("claude", "Analyse éditoriale (triple passe Claude)…"),
        )

        # Adaptive timeout — scales with page count and active passes to avoid
        # silently killing the pipeline on large documents or complete preset.
        # Formula: Pass1a rounds × 40s + Pass1b (60s) + Pass2 (90s) + 90s safety margin
        _total_pages = extraction.total_pages
        _batch_sz = 3 if _total_pages <= 30 else (4 if _total_pages <= 100 else 5)
        _effective_conc = 2 if _total_pages > 80 else MAX_CONCURRENT_BATCHES  # mirrors claude_corrector.py
        _n_rounds = math.ceil(math.ceil(_total_pages / _batch_sz) / _effective_conc)
        _pass1a = (_n_rounds * 40) if PASS1_NEEDED else 0
        _pass1b  = 60 if PASS1_NEEDED else 0
        _pass1c  = 60 if PASS1_NEEDED and _total_pages >= 8 else 0  # zones grises recheck
        _pass2   = 90 if PASS2_NEEDED else 0
        # +30s pour Passe 0 déjà exécutée avant wait_for, +120s marge (overlap +25-33% + Passe 1c)
        claude_timeout = max(180, _pass1a + _pass1b + _pass1c + _pass2 + 120)
        logger.info(
            "Timeout adaptatif — %ds (P1a=%ds P1b=%ds P1c=%ds P2=%ds +120s marge) pour %d pages",
            claude_timeout, _pass1a, _pass1b, _pass1c, _pass2, _total_pages,
        )

        try:
            claude_corrections, fact_check_dicts, claude_usage = await asyncio.wait_for(
                correct_document(
                    extraction.pages,
                    proper_noun_variants=proper_noun_variants,
                    doc_type=doc_type,
                    progress_callback=claude_progress,
                    enabled_categories=enabled_cats,
                    metadata=metadata_dict,
                ),
                timeout=float(claude_timeout),
            )
        except asyncio.TimeoutError:
            logger.error(
                "correct_document timeout (>%ds) — job %s | %d pages ; "
                "résultats partiels sauvegardés, pipeline continue vers l'annotation.",
                claude_timeout, job_id, extraction.total_pages,
            )
            _db_update(
                job_id,
                progress=78,
                progress_label=f"Analyse Claude interrompue (timeout {claude_timeout}s) — résultats partiels",
            )
            claude_corrections = []
            fact_check_dicts = []
            from backend.services.claude_corrector import _ApiUsage
            claude_usage = _ApiUsage()
        timer.done("claude")

        logger.info(
            "Claude triple passe : %d corrections A-G, %d faits à vérifier",
            len(claude_corrections), len(fact_check_dicts),
        )

        # Préparation des records Claude
        claude_records: list[dict] = []
        for cc in claude_corrections:
            cfg = CLAUDE_CONFIG.get(cc.category, CLAUDE_CONFIG["D"])
            claude_records.append({
                "page_number": cc.page_num,
                "category": cc.category,
                "original_text": cc.original_text,
                "corrected_text": cc.corrected_text,
                "description": (cc.description or "")[:200],
                "explanation": (cc.explanation or "")[:1000],
                "source": (cc.source or "")[:300],
                "confidence": cc.confidence,
                "annotation_type": cfg["annotation_type"],
                "color_r": cfg["color"][0],
                "color_g": cfg["color"][1],
                "color_b": cfg["color"][2],
            })

        _db_update(
            job_id, progress=78,
            progress_label=timer.label(
                "factcheck",
                f"Claude : {len(claude_records)} corrections — Vérification des faits…",
            ),
        )

        # ── 3. Fact-check — catégorie H ───────────────────────────────────────
        fact_records: list[dict] = []
        if not FACTCHECK_NEEDED:
            logger.info("Fact-check désactivé pour ce preset")
        else:
            timer.start("factcheck")
            if PASS1_NEEDED:
                # Mode normal : items depuis Claude + dates directes
                claude_fact_items = _build_fact_items(fact_check_dicts, max_items=20)
                date_items = _build_date_items_from_extraction(extraction, max_dates=20)
                existing_texts = {item.original_text.lower() for item in claude_fact_items}
                extra_dates = [d for d in date_items if d.original_text.lower() not in existing_texts]
                fact_items = claude_fact_items + extra_dates
                logger.info(
                    "Fact-check : %d items Claude + %d dates extraction = %d total",
                    len(claude_fact_items), len(extra_dates), len(fact_items),
                )
            else:
                # Mode H-seul : items directement depuis extraction
                fact_items = _build_all_fact_items_from_extraction(extraction, max_items=40)

            async def factcheck_progress(idx: int, total: int):
                pct_in_step = idx / max(total, 1)
                pct = 78 + int(pct_in_step * 10)
                _db_update(
                    job_id, progress=pct,
                    progress_label=timer.label(
                        "factcheck",
                        f"Vérification des faits — groupe {idx}/{total}",
                        pct_in_step,
                    ),
                )

            fact_corrections = await check_facts(
                fact_items,
                excluded_names=excluded_names,
                progress_callback=factcheck_progress,
            )
            timer.done("factcheck")
            logger.info("Fact-check : %d anomalie(s) détectée(s)", len(fact_corrections))

            for fc in fact_corrections:
                cfg_color = CATEGORY_H_CONFIG["color"]
                ann_type = CATEGORY_H_CONFIG["annotation_type"]
                fact_records.append({
                    "page_number": fc.page_num,
                    "category": fc.category,
                    "original_text": fc.original_text,
                    "corrected_text": fc.corrected_text,
                    "description": (fc.description or "")[:200],
                    "explanation": (fc.explanation or "")[:1000],
                    "source": (fc.source or "")[:300],
                    "confidence": fc.confidence,
                    "annotation_type": ann_type,
                    "color_r": cfg_color[0],
                    "color_g": cfg_color[1],
                    "color_b": cfg_color[2],
                })

        # ── 4. Filtrage des faux positifs puis déduplication ──────────────────
        raw_total = len(claude_records) + len(fact_records)
        combined = _filter_false_positives(claude_records + fact_records, extraction.full_text)
        all_records = _deduplicate(combined)

        # Log du ratio filtre — signal d'alerte si une passe génère trop de bruit
        if raw_total > 0:
            kept_ratio = len(all_records) / raw_total
            filter_ratio = 1.0 - len(combined) / raw_total
            logger.info(
                "Ratio qualité — brut=%d | après filtre=%d (−%.0f%%) | après déduplification=%d (conservation=%.0f%%)",
                raw_total, len(combined), filter_ratio * 100, len(all_records), kept_ratio * 100,
            )
            if filter_ratio > 0.40:
                logger.warning(
                    "⚠️  ALERTE QUALITÉ — %.0f%% des corrections ont été filtrées comme faux positifs. "
                    "Le prompt ou le filtre mérite une révision.",
                    filter_ratio * 100,
                )
        else:
            logger.info("Total après déduplication : %d corrections (aucune brute)", len(all_records))
        if all_records:
            _db_add_corrections(job_id, all_records)

        # ── 5. Annotation PDF (conditionnelle) ────────────────────────────────
        all_corrections = _db_get_corrections(job_id)

        if generate_pdf:
            _db_update(
                job_id, progress=88,
                progress_label=timer.label("annotate", "Génération du PDF annoté…"),
                status="annotating",
            )
            timer.start("annotate")
            annotation_requests = [
                AnnotationRequest(
                    page_num=c.page_number,
                    category=c.category,
                    original_text=re.sub(r'\*([^*]+)\*', r'\1', c.original_text),
                    corrected_text=c.corrected_text,
                    description=c.description or "",
                    explanation=c.explanation or "",
                    source=c.source or "",
                    confidence=c.confidence or "Probable",
                )
                for c in all_corrections
            ]

            # Sanitize stem so no crafted pdf_path can escape OUTPUT_DIR.
            raw_stem = Path(pdf_path).stem
            _safe_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
            safe_stem = "".join(c if c in _safe_chars else "_" for c in raw_stem) or "document"
            _PRESET_LABELS = {
                frozenset("ABCDEFGH"): "correction-complete",
                frozenset("ABCD"):     "correction-rapide",
                frozenset("EFG"):      "coherence-globale",
                frozenset("H"):        "verification-faits",
            }
            preset_label = _PRESET_LABELS.get(frozenset(enabled_cats), "correction")
            output_filename = f"{safe_stem}_{preset_label}.pdf"

            allowed_output_root = Path(settings.OUTPUT_DIR).resolve()
            output_dir = (allowed_output_root / job_id).resolve()
            try:
                output_dir.relative_to(allowed_output_root)
            except ValueError:
                raise RuntimeError(f"Output path escapes OUTPUT_DIR for job {job_id}")

            output_path = str(output_dir / output_filename)
            output_dir.mkdir(parents=True, exist_ok=True)

            loop = asyncio.get_running_loop()
            _annotate = functools.partial(
                annotate_pdf, doc_type=doc_type, preset_label=preset_label, comment_mode=comment_mode,
            )
            annotation_stats = await loop.run_in_executor(
                None, _annotate, pdf_path, output_path, annotation_requests,
            )
            timer.done("annotate")

            failure_rate = annotation_stats.get("failure_rate", 0)
            if failure_rate > 0.15:
                logger.warning(
                    "Taux d'échec annotation : %.0f%% — vérifier compatibilité PDF",
                    failure_rate * 100,
                )

            annotated_in_pdf = annotation_stats.get("annotated", len(all_corrections))
            h_not_annotated_n = annotation_stats.get("h_not_annotated", 0)
            final_output_path: str | None = output_path
        else:
            annotated_in_pdf = len(all_corrections)
            h_not_annotated_n = 0
            final_output_path = None

        # ── 6. Résolution des bbox ─────────────────────────────────────────────
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _resolve_correction_bboxes, job_id, pdf_path)

        # ── 7. Finalisation ────────────────────────────────────────────────────
        cat_counts: dict[str, int] = {}
        for c in all_corrections:
            cat_counts[c.category] = cat_counts.get(c.category, 0) + 1
        total = len(all_corrections)

        actual_cost = round(claude_usage.cost_usd(), 5)

        _db_update(
            job_id,
            status="done",
            progress=100,
            progress_label=(
                f"Terminé — {total} correction{'s' if total > 1 else ''}"
                + (f" ({annotated_in_pdf} dans le PDF)" if generate_pdf else "")
            ),
            output_pdf_path=final_output_path,
            corrections_count=total,
            corrections_by_category=cat_counts,
            annotated_count=annotated_in_pdf,
            h_not_annotated_count=h_not_annotated_n,
            actual_cost_usd=actual_cost,
            actual_tokens_input=claude_usage.input_tokens,
            actual_tokens_output=claude_usage.output_tokens,
            actual_tokens_cache_read=claude_usage.cache_read_tokens,
        )
        logger.info("Pipeline terminé — job %s | %d corrections | %s", job_id, total, cat_counts)
        _update_user_credit(user_id, actual_cost)

    except Exception as exc:
        logger.exception("Pipeline échoué — job %s", job_id)
        _db_update(
            job_id,
            status="error",
            progress=0,
            progress_label="Erreur lors du traitement",
            error_message=str(exc)[:2000],
        )
