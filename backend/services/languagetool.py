"""
LanguageTool — CATÉGORIE C UNIQUEMENT (typographie déterministe).

Option C retenue : LT traite exclusivement la typographie (C), Claude gère A/B/C/D/E/F/G
avec connaissance du contexte.

Deux filtres anti-faux-positifs :
  1. Lignes décoratives : textes composés uniquement de points, tirets, espaces répétés → ignorés
  2. Blocs titres : règles de ponctuation finale ignorées sur les gros blocs (police titre)

Self-hosted Docker (production, sans limite de débit) :
  docker run -p 8010:8010 silviof/docker-languagetool
  Puis : LANGUAGETOOL_URL=http://localhost:8010/v2
"""
from __future__ import annotations

import asyncio
import logging
import re
import ssl
from dataclasses import dataclass
from typing import Any

import aiohttp
import certifi

from backend.config import settings

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
logger = logging.getLogger(__name__)

LT_CHUNK_SIZE = 15_000

# ── Seule catégorie traitée par LT ───────────────────────────────────────────
CATEGORY_CONFIG: dict[str, dict] = {
    "C": {
        "name": "C – Typographie",
        "color": (0.78, 0.60, 1.00),
        "annotation_type": "Highlight",
    },
}

# ── Règles LT qui correspondent à de la typographie pure ─────────────────────
# On inclut uniquement les règles dont la correction est context-free
# (un guillemet droit est TOUJOURS faux, une espace insécable manquante est TOUJOURS fausse)
TYPOGRAPHY_RULE_IDS: set[str] = {
    # Guillemets
    "GUILLEMETS", "GUILLEMETS_USAGE", "GUILLEMETS_SPACE",
    "UNPAIRED_BRACKETS",
    # Apostrophes
    "APOSTROPHE_TYPOGRAPHIQUE", "APOSTROPHE_INCORRECTE",
    # Espaces insécables
    "ESPACE_AVANT_DOUBLE_PONCTUATION",
    "FRENCH_WHITESPACE",
    "NBSP_AVANT_DOUBLE_PONCTUATION",
    "ESPACE_INSECABLE",
    # Points de suspension
    "POINTS_SUSPENSION",
    "TRIPLE_DOTS",
    # Doubles espaces / ponctuation double
    "DOUBLE_PUNCTUATION",
    "WHITESPACE_RULE",
    "WHITESPACE_BEFORE_PUNCTUATION",
    # Tirets
    "TIRET_CADRANT", "TIRET_DEMI_CADRANT",
    # Règles françaises spécifiques
    "POINT", "PONCTUATION_FINALE",
}

TYPOGRAPHY_CATEGORY_IDS: set[str] = {
    "TYPOGRAPHY", "PUNCTUATION", "WHITESPACE", "FORMATTING",
    "TYPO", "CAT_TYPOGRAPHIE", "PONCTUATION_TYPOGRAPHIQUE",
}

TYPOGRAPHY_RULE_KEYWORDS: list[str] = [
    "GUILLEMET", "APOSTROPHE", "ESPACE", "NBSP", "TIRET",
    "PONCTUATION", "QUOTE", "WHITESPACE", "SUSPENSION",
]

# ── Filtre 1 : lignes décoratives ─────────────────────────────────────────────
# Texte composé quasi-exclusivement de caractères répétitifs (points, tirets, espaces)
_DECORATIVE_RE = re.compile(r"^[\s.\-–—_·•…]{3,}$")

def _is_decorative(text: str) -> bool:
    """Retourne True si le texte est une ligne décorative (points de suspens, tirets, etc.)."""
    stripped = text.strip()
    if not stripped:
        return True
    # Séquence de 3+ caractères identiques ou quasi-identiques
    if _DECORATIVE_RE.match(stripped):
        return True
    # Plus de 60% de points ou tirets
    punct_count = sum(1 for c in stripped if c in ".-–—_·•… ")
    if len(stripped) > 2 and punct_count / len(stripped) > 0.6:
        return True
    return False

# ── Filtre 2 : règles de ponctuation finale sur titres ───────────────────────
# Règles qui signalent une ponctuation manquante en FIN de phrase
_SENTENCE_END_RULES: set[str] = {
    "POINT", "PONCTUATION_FINALE", "UNPAIRED_BRACKETS",
    "UPPERCASE_SENTENCE_START",
}

def _is_title_context(context_text: str) -> bool:
    """
    Heuristique : le contexte ressemble-t-il à un titre ?
    Un titre est court (< 80 chars hors espaces) et ne contient pas de verbe conjugué évident.
    """
    stripped = context_text.strip()
    # Court
    if len(stripped) < 80:
        # Pas de point en fin (c'est justement ce qu'on cherche à ignorer pour les titres)
        # Pas de verbe auxiliaire (heuristique simple)
        lower = stripped.lower()
        has_verb_marker = any(
            marker in lower
            for marker in [" est ", " sont ", " était ", " avait ", " ont ", " a ", " il ", " elle "]
        )
        if not has_verb_marker:
            return True
    return False

# ── Mapping règle LT → catégorie C ───────────────────────────────────────────

def _is_typography_rule(rule_id: str, lt_category_id: str) -> bool:
    """Retourne True si cette règle LT correspond à de la typographie pure (catégorie C)."""
    if rule_id in TYPOGRAPHY_RULE_IDS:
        return True
    if lt_category_id.upper() in TYPOGRAPHY_CATEGORY_IDS:
        return True
    rule_up = rule_id.upper()
    cat_up = lt_category_id.upper()
    for kw in TYPOGRAPHY_RULE_KEYWORDS:
        if kw in rule_up or kw in cat_up:
            return True
    return False

# ── Règles toujours ignorées ──────────────────────────────────────────────────
SKIP_RULES: set[str] = {
    "UPPERCASE_SENTENCE_START",
    "SENTENCE_WHITESPACE",
    "MORFOLOGIK_RULE_FR",
    "FR_SPELLING_RULE",       # orthographe → Claude s'en charge
}

SKIP_MESSAGE_FRAGMENTS: list[str] = [
    "is a foreign word",
    "mot étranger",
]


@dataclass
class LTCorrection:
    page_num: int
    category: str          # toujours "C"
    original_text: str
    corrected_text: str | None
    description: str
    explanation: str
    source: str
    offset: int
    length: int
    lt_rule_id: str


async def _check_chunk(
    session: aiohttp.ClientSession,
    text: str,
    language: str = "fr",
) -> list[dict[str, Any]]:
    url = f"{settings.LANGUAGETOOL_URL}/check"
    data = {
        "text": text,
        "language": language,
        "enabledOnly": "false",
        "level": "picky",
    }
    try:
        async with session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=45), ssl=_SSL_CTX) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.warning("LanguageTool %d : %s", resp.status, body[:200])
                return []
            result = await resp.json()
            matches = result.get("matches", [])
            logger.info("LanguageTool : %d correspondances pour %d caractères", len(matches), len(text))
            return matches
    except asyncio.TimeoutError:
        logger.warning("LanguageTool : délai dépassé")
        return []
    except Exception as exc:
        logger.warning("LanguageTool : erreur de connexion — %s", exc)
        return []


async def check_page(
    page_num: int,
    page_text: str,
) -> list[LTCorrection]:
    """Analyse une page avec LanguageTool et retourne uniquement les corrections typographiques (C)."""
    if not page_text.strip():
        return []

    corrections: list[LTCorrection] = []

    async with aiohttp.ClientSession() as session:
        chunks: list[tuple[int, str]] = []
        offset = 0
        while offset < len(page_text):
            chunk = page_text[offset: offset + LT_CHUNK_SIZE]
            chunks.append((offset, chunk))
            offset += LT_CHUNK_SIZE

        for chunk_offset, chunk_text in chunks:
            matches = await _check_chunk(session, chunk_text)
            for match in matches:
                rule = match.get("rule", {})
                rule_id: str = rule.get("id", "UNKNOWN")
                lt_category_id: str = rule.get("category", {}).get("id", "MISC")
                message: str = match.get("message", "")
                context_info: dict = match.get("context", {})
                context_text: str = context_info.get("text", "")

                # ── Filtres de base ──────────────────────────────────────────
                if rule_id in SKIP_RULES:
                    continue
                if any(frag in message for frag in SKIP_MESSAGE_FRAGMENTS):
                    continue

                # ── Filtre : uniquement typographie ─────────────────────────
                if not _is_typography_rule(rule_id, lt_category_id):
                    continue

                offset_in_chunk: int = match["offset"]
                length: int = match["length"]
                if length == 0:
                    continue

                original = chunk_text[offset_in_chunk: offset_in_chunk + length]
                if not original.strip():
                    continue

                # ── Filtre 1 : lignes décoratives ────────────────────────────
                if _is_decorative(original):
                    logger.debug("LT ignoré (décoratif) : %r", original[:40])
                    continue

                # ── Filtre 2 : ponctuation finale sur titre ──────────────────
                if rule_id in _SENTENCE_END_RULES and _is_title_context(context_text):
                    logger.debug("LT ignoré (titre) : %r | règle %s", original[:40], rule_id)
                    continue

                absolute_offset = chunk_offset + offset_in_chunk
                replacements = match.get("replacements", [])
                corrected = replacements[0]["value"] if replacements else None
                short_message: str = match.get("shortMessage", "") or message

                corrections.append(LTCorrection(
                    page_num=page_num,
                    category="C",
                    original_text=original,
                    corrected_text=corrected,
                    description=short_message[:120],
                    explanation=f"{message}\n\nContexte : « {context_text} »",
                    source=f"LanguageTool — règle : {rule_id}",
                    offset=absolute_offset,
                    length=length,
                    lt_rule_id=rule_id,
                ))

    logger.info("Page %d : %d corrections typographiques (LT)", page_num + 1, len(corrections))
    return corrections


async def check_document(pages: list) -> list[LTCorrection]:
    """Analyse toutes les pages avec LanguageTool (catégorie C uniquement)."""
    sem = asyncio.Semaphore(2)

    async def check_with_sem(page) -> list[LTCorrection]:
        async with sem:
            await asyncio.sleep(0.3)
            return await check_page(page.page_num, page.text)

    tasks = [check_with_sem(p) for p in pages]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_corrections: list[LTCorrection] = []
    for res in results:
        if isinstance(res, list):
            all_corrections.extend(res)
        else:
            logger.warning("Erreur LT page : %s", res)

    logger.info("LanguageTool total : %d corrections typographiques", len(all_corrections))
    return all_corrections
