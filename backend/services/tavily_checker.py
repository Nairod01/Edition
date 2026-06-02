"""
Tavily — Vérification des faits (catégorie H).
Pilotée par Claude : on ne vérifie que ce que Claude a jugé incertain.

Améliorations :
  - Requêtes ciblées vers sources françaises (Wikipedia FR, Babelio, Larousse…)
  - Matching par mots-clés avec normalisation des accents (gère les réponses en anglais)
  - Exclusion des noms propres attestés dans le document (ne jamais flaguer un auteur du livre lui-même)
"""
from __future__ import annotations

import asyncio
import logging
import re
import ssl
import unicodedata
from dataclasses import dataclass
from typing import Any

import aiohttp
import certifi

from backend.config import settings

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
logger = logging.getLogger(__name__)

TAVILY_API_URL = "https://api.tavily.com/search"
TAVILY_CONCURRENCY = 3

# Sources francophones privilégiées pour les requêtes littéraires
FRENCH_DOMAINS = [
    "fr.wikipedia.org",
    "babelio.com",
    "larousse.fr",
    "data.bnf.fr",
    "gallimard.fr",
    "editions-allia.com",
    "academie-francaise.fr",
]

CATEGORY_H_CONFIG = {
    "name": "H – Vérification des faits",
    "color": (1.00, 0.92, 0.40),
    "annotation_type": "Highlight",
}

MIN_ITEM_LENGTH = 4
# Seuil de correspondance par mots-clés (60% des mots significatifs doivent matcher)
KEYWORD_MATCH_THRESHOLD = 0.55


@dataclass
class FactCheckItem:
    query: str
    context: str
    page_num: int
    original_text: str
    item_type: str   # "date" | "proper_noun" | "title"


@dataclass
class TavilyCorrection:
    page_num: int
    category: str
    original_text: str
    corrected_text: str | None
    description: str
    explanation: str
    source: str
    confidence: str   # "high" | "medium" | "low"


# ── Normalisation pour matching multilingue ────────────────────────────────────

def _remove_accents(text: str) -> str:
    """Supprime les accents pour comparaison accent-insensible."""
    return ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    )


def _keyword_match(
    original: str,
    combined: str,
    threshold: float = KEYWORD_MATCH_THRESHOLD,
    strict: bool = False,
) -> bool:
    """
    Retourne True si suffisamment de mots-clés significatifs de `original`
    sont trouvés dans `combined`.

    strict=True  (noms propres) : matching exact uniquement — évite de confondre
                                   "Hugot" avec "Hugo", "Dalís" avec "Dalí".
    strict=False (titres/dates) : préfixe 5 chars autorisé pour gérer les cognates
                                   franco-anglais (persistance → persistence).
    """
    words = re.findall(r'\b[a-zA-ZÀ-ÿ]{4,}\b', original)
    if not words:
        return True  # pas de mots significatifs → on ne peut pas vérifier

    combined_norm = _remove_accents(combined.lower())
    matches: float = 0.0

    for w in words:
        w_norm = _remove_accents(w.lower())
        if w_norm in combined_norm:
            matches += 1.0
        elif not strict and len(w_norm) >= 5 and w_norm[:5] in combined_norm:
            # Préfixe 5 chars : "persis" ∈ "persistence" ✓
            matches += 0.7
        elif not strict and len(w_norm) >= 4 and w_norm[:4] in combined_norm:
            # Préfixe 4 chars : "memo" ∈ "memory" ✓ (mémoire → memory)
            matches += 0.5

    return (matches / len(words)) >= threshold


# ── Filtres ────────────────────────────────────────────────────────────────────

def _is_valid_for_fact_check(text: str) -> bool:
    text = text.strip()
    if len(text) < MIN_ITEM_LENGTH:
        return False
    if re.match(r"^\d+$", text):
        return False
    if re.match(r"^[A-Z0-9.\-]{1,6}$", text):
        return False
    if not re.search(r"[a-zA-ZÀ-ÿ]", text):
        return False
    return True


# ── Construction des requêtes ──────────────────────────────────────────────────

def _build_query(item: FactCheckItem) -> str:
    """Requêtes optimisées pour les sources françaises."""
    if item.item_type == "date":
        return f"{item.context} date exacte historique"
    elif item.item_type == "title":
        # Requête directe sur le titre avec mots-clés littéraires
        return f"{item.original_text} livre roman auteur date publication"
    else:  # proper_noun
        return f"{item.original_text} écrivain auteur biographie"


# ── Recherche Tavily ───────────────────────────────────────────────────────────

async def _tavily_search(
    session: aiohttp.ClientSession,
    query: str,
    max_results: int = 5,
    use_french_domains: bool = True,
) -> tuple[list[dict[str, Any]], str]:
    """Recherche Tavily, ciblée sur les sources françaises par défaut."""
    payload: dict[str, Any] = {
        "api_key": settings.TAVILY_API_KEY,
        "query": query,
        "search_depth": "basic",
        "max_results": max_results,
        "include_answer": True,
        "include_raw_content": False,
    }
    if use_french_domains:
        payload["include_domains"] = FRENCH_DOMAINS

    try:
        async with session.post(
            TAVILY_API_URL,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=15),
            ssl=_SSL_CTX,
        ) as resp:
            if resp.status != 200:
                logger.warning("Tavily %d pour : %s", resp.status, query[:80])
                return [], ""
            data = await resp.json()
            results = data.get("results", [])
            answer = data.get("answer", "") or ""

            # Si aucun résultat avec domaines français, relancer sans restriction
            if not results and use_french_domains:
                return await _tavily_search(session, query, max_results, use_french_domains=False)

            return results, answer
    except asyncio.TimeoutError:
        logger.warning("Tavily : délai dépassé pour : %s", query[:80])
        return [], ""
    except Exception as exc:
        logger.warning("Tavily : erreur pour « %s » — %s", query[:60], exc)
        return [], ""


# ── Extraction de la valeur correcte ──────────────────────────────────────────

def _extract_correct_date(original: str, answer: str, snippets: str) -> str | None:
    combined = answer + " " + snippets
    patterns = [
        r'\b\d{1,2}\s+(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\s+\d{4}\b',
        r'\b(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\s+\d{4}\b',
        r'\b\d{4}\b',
    ]
    for pattern in patterns:
        m = re.search(pattern, answer, re.IGNORECASE)
        if m:
            found = m.group(0).strip()
            if found.lower().replace(" ", "") != original.lower().strip("()").replace(" ", ""):
                return found
    return None


def _extract_correct_name(original: str, results: list[dict], answer: str) -> str | None:
    original_norm = _remove_accents(original.lower())
    for result in results:
        title = result.get("title", "")
        # Cherche des noms propres dans le titre du résultat
        proper_nouns = re.findall(
            r'\b[A-ZÀ-Ü][a-zà-ü\-]{2,}(?:\s+[A-ZÀ-Ü][a-zà-ü\-]{2,})+\b', title
        )
        for pn in proper_nouns:
            pn_norm = _remove_accents(pn.lower())
            # Proche mais différent → candidat à la correction
            if (pn_norm != original_norm
                    and abs(len(pn) - len(original)) <= 4
                    and pn_norm[:4] == original_norm[:4]):
                return pn
    return None


def _extract_correct_title(original: str, results: list[dict], answer: str) -> str | None:
    original_norm = _remove_accents(original.lower())
    original_words = set(re.findall(r'\b[a-zA-ZÀ-ÿ]{4,}\b', original))
    for result in results:
        for text in [result.get("title", ""), result.get("content", "")[:300]]:
            # Cherche des titres entre guillemets ou italiques
            for m in re.finditer(r'[«""]([^»""]{5,80})[»""]', text):
                candidate = m.group(1).strip()
                cand_words = set(re.findall(r'\b[a-zA-ZÀ-ÿ]{4,}\b', candidate))
                # Partage assez de mots avec l'original
                if (original_words & cand_words
                        and candidate.lower() != original.lower()):
                    return candidate
    return None


# ── Analyse des résultats ──────────────────────────────────────────────────────

def _analyze_result(
    item: FactCheckItem,
    results: list[dict],
    answer: str,
) -> TavilyCorrection | None:
    """
    Retourne une correction UNIQUEMENT si anomalie détectée.
    Silence si le fait est confirmé.
    Utilise keyword matching accent-insensible pour éviter les faux négatifs
    dus aux réponses en anglais.
    """
    snippets = " ".join(r.get("content", "")[:300] for r in results[:5])
    combined = snippets + " " + answer
    top = results[0] if results else None
    url = top.get("url", "") if top else ""

    # ── Nom propre ─────────────────────────────────────────────────────────────
    if item.item_type == "proper_noun":
        if not results:
            return None  # inconnu ≠ faux
        # Matching strict pour les noms propres (pas de préfixe — "Hugot" ≠ "Hugo")
        if _keyword_match(item.original_text, combined, strict=True):
            return None  # trouvé → silence
        correct = _extract_correct_name(item.original_text, results, answer)
        return TavilyCorrection(
            page_num=item.page_num,
            category="A",
            original_text=item.original_text,
            corrected_text=correct,
            description="Orthographe du nom propre à vérifier",
            explanation=(
                f"Le nom « {item.original_text} » n'a pas été trouvé verbatim dans les sources.\n"
                + (f"Forme trouvée : « {correct} »\n" if correct else "")
                + f"\nExtrait : {snippets[:250]}"
            ),
            source=f"Tavily : {url}" if url else "Tavily web search",
            confidence="medium",
        )

    # ── Titre d'œuvre ──────────────────────────────────────────────────────────
    if item.item_type == "title":
        if not results:
            return None  # pas de résultats → on ne sait pas → silence
        # Matching souple pour les titres (préfixe autorisé : gère FR/EN)
        if _keyword_match(item.original_text, combined, strict=False):
            return None  # titre confirmé → silence
        correct = _extract_correct_title(item.original_text, results, answer)
        return TavilyCorrection(
            page_num=item.page_num,
            category="H",
            original_text=item.original_text,
            corrected_text=correct,
            description="Titre d'œuvre introuvable — à vérifier",
            explanation=(
                f"Le titre « {item.original_text} » n'a pas été confirmé par les sources.\n"
                + (f"Titre probable : « {correct} »\n" if correct else "")
                + f"\nRéponse Tavily : {(answer or snippets)[:250]}"
            ),
            source=f"Tavily : {url}" if url else "Tavily web search",
            confidence="medium",
        )

    # ── Date historique ────────────────────────────────────────────────────────
    if item.item_type == "date":
        if not results:
            return TavilyCorrection(
                page_num=item.page_num,
                category="H",
                original_text=item.original_text,
                corrected_text=None,
                description="Date non vérifiable en ligne",
                explanation=(
                    f"Aucune source trouvée pour : « {item.original_text} ».\n"
                    f"Contexte : {item.context}\nVérification manuelle recommandée."
                ),
                source="Tavily web search — aucun résultat",
                confidence="low",
            )
        # Keyword match sur la date (gère formats variés)
        date_norm = _remove_accents(item.original_text.lower().replace(" ", ""))
        combined_norm = _remove_accents(combined.lower().replace(" ", ""))
        if date_norm in combined_norm:
            return None  # date confirmée → silence

        # Date non confirmée mais résultats disponibles → signaler avec piste
        correct = _extract_correct_date(item.original_text, answer, snippets)
        return TavilyCorrection(
            page_num=item.page_num,
            category="H",
            original_text=item.original_text,
            corrected_text=correct,
            description="Date non confirmée — à vérifier",
            explanation=(
                f"La date « {item.original_text} » n'est pas confirmée par les sources.\n"
                + (f"Date probable : {correct}\n" if correct else "")
                + f"Contexte : {item.context}\n\nRéponse Tavily : {(answer or snippets)[:300]}"
            ),
            source=f"Tavily : {url}" if url else "Tavily web search",
            confidence="medium",
        )

    return None


# ── Point d'entrée public ──────────────────────────────────────────────────────

async def check_facts(
    items: list[FactCheckItem],
    excluded_names: set[str] | None = None,
    progress_callback=None,
) -> list[TavilyCorrection]:
    """
    Vérifie les faits via Tavily.
    excluded_names : noms propres attestés dans le document → jamais envoyés à Tavily.
    Retourne uniquement les anomalies détectées.
    """
    if not items:
        return []

    # Filtre 1 : validité de base
    valid = [i for i in items if _is_valid_for_fact_check(i.original_text)]

    # Filtre 2 : exclusion des noms propres attestés dans le document
    if excluded_names:
        before = len(valid)
        valid = [
            i for i in valid
            if not (
                i.item_type == "proper_noun"
                and i.original_text in excluded_names
            )
        ]
        excluded_count = before - len(valid)
        if excluded_count > 0:
            logger.info(
                "Tavily : %d nom(s) propre(s) attesté(s) dans le document exclus de la vérification",
                excluded_count,
            )

    logger.info(
        "Tavily : %d éléments à vérifier (%d filtrés au total)",
        len(valid), len(items) - len(valid),
    )

    if not valid:
        return []

    sem = asyncio.Semaphore(TAVILY_CONCURRENCY)

    async with aiohttp.ClientSession() as session:
        async def check_one(item: FactCheckItem, idx: int) -> TavilyCorrection | None:
            async with sem:
                if progress_callback and idx % 3 == 0:
                    await progress_callback(idx, len(valid))
                results, answer = await _tavily_search(session, _build_query(item))
                return _analyze_result(item, results, answer)

        tasks = [check_one(item, i) for i, item in enumerate(valid)]
        raw = await asyncio.gather(*tasks, return_exceptions=True)

    corrections = [r for r in raw if isinstance(r, TavilyCorrection)]
    errors = [r for r in raw if isinstance(r, Exception)]
    for e in errors:
        logger.warning("Tavily exception : %s", e)

    logger.info("Tavily : %d anomalies sur %d éléments vérifiés", len(corrections), len(valid))
    return corrections
