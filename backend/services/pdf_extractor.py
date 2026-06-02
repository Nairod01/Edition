"""
PDF text extraction using PyMuPDF.
Extracts text per page with character-level position data for later annotation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF


@dataclass
class PageText:
    page_num: int          # 0-indexed
    text: str              # plain text
    word_count: int
    blocks: list[dict] | None = None  # raw fitz blocks with position data (None if include_blocks=False)


@dataclass
class ExtractionResult:
    pages: list[PageText]
    total_pages: int
    total_words: int
    full_text: str         # concatenation of all pages (with page markers)
    # Preliminary census — only genuine verifiable items
    proper_nouns: list[str] = field(default_factory=list)   # multi-word: "Victor Hugo", "C. S. Lewis"
    dates: list[dict] = field(default_factory=list)          # {"text": "...", "context": "...", "page": n}
    defined_terms: list[str] = field(default_factory=list)
    placeholders: list[str] = field(default_factory=list)
    titles: list[dict] = field(default_factory=list)         # {"text": "...", "page": n}


# ── Patterns ─────────────────────────────────────────────────────────────────

# Dates with FULL context: "le 12 mars 1456", "en 1789", "14 juillet 1789"
# Requires a month name OR a day number alongside the year — avoids bare page numbers
_MONTHS_FR = (
    "janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre"
    "|january|february|march|april|june|july|august|september|october|november|december"
)
_DATE_WITH_MONTH = re.compile(
    rf"\b(?:\d{{1,2}}\s+)?(?:{_MONTHS_FR})\s+\d{{4}}\b",
    re.IGNORECASE,
)
_DATE_DD_MM_YYYY = re.compile(r"\b\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4}\b")
# Year alone only when preceded by "en ", "En ", "vers ", "(", etc.
_DATE_YEAR_ALONE = re.compile(r"(?<=\ben\s)\d{4}\b|(?<=\bvers\s)\d{4}\b|\(\d{4}\)")

# Multi-word proper nouns: two or more consecutive capitalized words
# e.g. "Victor Hugo", "Charles de Gaulle", "Marie de Médicis"
_PROPER_NOUN_MULTI = re.compile(
    r"\b([A-ZÀ-Ü][a-zà-ü\-]{1,}(?:\s+(?:de\s+|d\')?[A-ZÀ-Ü][a-zà-ü\-]{1,}){1,3})\b"
)

# Titles of works in guillemets (≥ 2 words)
_TITLE_GUILLEMETS = re.compile(r"«\s*([^»]{8,100})\s*»")
# Titles in italics markers or underscores
_TITLE_ITALIC = re.compile(r"_([^_]{8,80})_")

# Placeholders
_PLACEHOLDER_RE = re.compile(
    r"\b(?:XX+[-–]?XX*|X{2,}|Xxxxx+|ILLU\s*\w*|TBD|TODO|À\s+COMPLÉTER|N\.?A\.?)\b",
    re.IGNORECASE,
)

def _join_hyphens(text: str) -> str:
    """
    Supprime les artefacts de coupure dans le texte extrait d'un PDF mis en page,
    pour que Claude reçoive un texte propre sans tirets parasites.

    Étape 1 — tirets conditionnels invisibles (U+00AD, soft hyphen) :
        « socio\xadlogie » → « sociologie »

    Étape 2 — coupures explicites en fin de ligne (tiret + saut de ligne) :
        « contem-\nporains » → « contemporains »
        « piteu-\nsement »  → « piteusement »
        Le tiret ET le saut de ligne sont supprimés : Claude ne voit pas d'artefact
        et ne génère pas de faux positif « coupure syllabique ».
        Seules les liaisons minuscule → minuscule sont traitées (évite les acronymes).
    """
    text = text.replace('\xad', '')  # étape 1 : supprimer les soft hyphens invisibles
    text = re.sub(r'-\n([a-zàâäéèêëîïôùûüçœæ])', r'-\1', text)  # étape 2 : joindre en conservant le tiret (soi-même, c'est-à-dire…)
    text = re.sub(r' {2,}', ' ', text)  # étape 3 : normaliser les espaces multiples (justification PDF)
    # étape 4 : joindre les retours à la ligne dans le flux justifié
    # (minuscule ou ponctuation → minuscule) = saut de ligne de justification, pas un vrai ¶
    text = re.sub(r'([a-zàâäéèêëîïôùûüçœæ,;:])\n([a-zàâäéèêëîïôùûüçœæ])', r'\1 \2', text)
    return text


# Common French words to EXCLUDE from proper noun list
_COMMON_WORDS = {
    "Le", "La", "Les", "Un", "Une", "Des", "Du", "De", "En", "Au", "Aux",
    "Par", "Sur", "Sous", "Dans", "Et", "Ou", "Mais", "Car", "Donc", "Or",
    "Que", "Qui", "Dont", "Où", "Si", "Ne", "Ni", "Car", "Cela", "Cette",
    "Ces", "Son", "Sa", "Ses", "Mon", "Ma", "Mes", "Ton", "Ta", "Tes",
    "Notre", "Votre", "Leur", "Leurs", "Avec", "Pour", "Vers", "Tout",
    "Plus", "Bien", "Très", "Aussi", "Ainsi", "Donc", "Lors", "Puis",
    "Après", "Avant", "Depuis", "Pendant", "Entre", "Parmi", "Selon",
    "Voici", "Voilà", "Comme", "Même", "Chez",
}


def _is_valid_proper_noun(name: str) -> bool:
    """Filter out common words and too-short strings from proper nouns."""
    # No newlines allowed (PDF extraction artifact)
    if "\n" in name or "\r" in name:
        return False
    words = name.split()
    if len(words) < 2:
        return False  # single words only OK if clearly a known name — skip them
    # Remove if all words are common
    if all(w in _COMMON_WORDS for w in words):
        return False
    # Must have at least one word that looks like a proper name (>= 3 chars)
    if not any(len(w) >= 3 for w in words):
        return False
    # No word should be a single letter (except initials like "C. S.")
    if any(len(w) == 1 and not w.isupper() for w in words):
        return False
    return True


def _get_page_text_no_large_font(page: "fitz.Page", size_threshold: float = 36.0) -> str:
    """
    Reconstruit le texte de la page en excluant les spans dont la taille de police
    dépasse `size_threshold` points.

    Utilisé pour les livres d'art (beaux_arts) afin d'éviter que les filigranes
    décoratifs (noms d'artistes en très grands caractères, souvent > 60 pt)
    ne soient envoyés à Claude et signalés à tort comme fautes.
    N'affecte PAS les blocs de position — l'annotation PDF reste inchangée.
    """
    result_parts: list[str] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:  # ignorer les blocs image
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            # Ignorer les lignes entièrement composées de grandes polices
            visible_spans = [s for s in spans if s.get("text", "").strip()]
            if visible_spans and all(s.get("size", 0) > size_threshold for s in visible_spans):
                continue
            line_text = "".join(
                s.get("text", "")
                for s in spans
                if s.get("size", 0) <= size_threshold
            )
            if line_text.strip():
                result_parts.append(line_text)
    return "\n".join(result_parts)


def extract(pdf_path: str | Path, include_blocks: bool = True, filter_large_font: bool = False) -> ExtractionResult:
    """Extract text and metadata from a PDF file.

    Parameters
    ----------
    pdf_path:
        Path to the PDF file.
    include_blocks:
        When True (default), each PageText stores raw fitz block data needed by the
        PDF annotator.  Set to False to skip block extraction and save ~60 % memory
        for very long documents when annotation is not required.
        Note: pipeline.py always passes include_blocks=True since the annotator needs
        block-level position data.
    filter_large_font:
        When True, rebuild each page's text by excluding spans with font size > 36 pt.
        This removes decorative filigrane text (artist names printed as large background
        typography in art books) that PyMuPDF extracts but Claude would flag as errors.
        Annotation blocks (used for PDF highlighting) are NOT affected — only the text
        sent to Claude is filtered.  Activated for doc_type == 'beaux_arts' in pipeline.py.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF introuvable : {pdf_path}")

    doc = fitz.open(str(pdf_path))

    if len(doc) == 0:
        doc.close()
        raise ValueError(f"Le PDF est vide (0 pages) : {pdf_path.name}")

    pages: list[PageText] = []
    all_text_parts: list[str] = []
    proper_nouns_seen: set[str] = set()
    dates: list[dict] = []
    placeholders: list[str] = []
    titles: list[dict] = []

    try:
        for page_idx in range(len(doc)):
            page = doc[page_idx]

            if filter_large_font:
                raw_text = _get_page_text_no_large_font(page)
            else:
                raw_text = page.get_text("text")
            text = _join_hyphens(raw_text)
            blocks: list[dict[str, Any]] | None = None
            if include_blocks:
                blocks = page.get_text("dict")["blocks"]  # type: ignore[assignment]

            words = text.split()
            pages.append(PageText(
                page_num=page_idx,
                text=text,
                word_count=len(words),
                blocks=blocks,
            ))

            page_marker = f"\n\n[PAGE {page_idx + 1}]\n"
            all_text_parts.append(page_marker + text)

            # ── Dates with context ────────────────────────────────────────────
            for m in _DATE_WITH_MONTH.finditer(text):
                date_str = m.group(0).strip()
                start = max(0, m.start() - 60)
                end = min(len(text), m.end() + 60)
                context = text[start:end].replace("\n", " ").strip()
                dates.append({"text": date_str, "context": context, "page": page_idx})

            for m in _DATE_DD_MM_YYYY.finditer(text):
                date_str = m.group(0).strip()
                start = max(0, m.start() - 60)
                end = min(len(text), m.end() + 60)
                context = text[start:end].replace("\n", " ").strip()
                dates.append({"text": date_str, "context": context, "page": page_idx})

            for m in _DATE_YEAR_ALONE.finditer(text):
                date_str = m.group(0).strip("()")
                start = max(0, m.start() - 80)
                end = min(len(text), m.end() + 80)
                context = text[start:end].replace("\n", " ").strip()
                dates.append({"text": date_str, "context": context, "page": page_idx})

            # ── Multi-word proper nouns ───────────────────────────────────────
            for m in _PROPER_NOUN_MULTI.finditer(text):
                name = m.group(1).strip()
                if _is_valid_proper_noun(name) and name not in proper_nouns_seen:
                    proper_nouns_seen.add(name)

            # ── Titles in guillemets ──────────────────────────────────────────
            for m in _TITLE_GUILLEMETS.finditer(text):
                title_text = m.group(1).strip()
                # Must have at least 2 words
                if len(title_text.split()) >= 2:
                    titles.append({"text": title_text, "page": page_idx})

            for m in _TITLE_ITALIC.finditer(text):
                title_text = m.group(1).strip()
                if len(title_text.split()) >= 2:
                    titles.append({"text": title_text, "page": page_idx})

            # ── Placeholders ─────────────────────────────────────────────────
            for m in _PLACEHOLDER_RE.finditer(text):
                placeholders.append(m.group(0).strip())

    finally:
        doc.close()

    full_text = "".join(all_text_parts)
    total_words = sum(p.word_count for p in pages)

    # Deduplicate titles
    seen_titles: set[str] = set()
    unique_titles = []
    for t in titles:
        if t["text"] not in seen_titles:
            seen_titles.add(t["text"])
            unique_titles.append(t)

    # Cap items to avoid too many API calls
    return ExtractionResult(
        pages=pages,
        total_pages=len(pages),
        total_words=total_words,
        full_text=full_text,
        proper_nouns=sorted(proper_nouns_seen)[:80],
        dates=dates[:30],
        defined_terms=[],
        placeholders=list(set(placeholders)),
        titles=unique_titles[:30],
    )


def estimate_cost(extraction: ExtractionResult, input_price: float, output_price: float) -> dict:
    """
    Estimation du coût Claude API.
    Tient compte de la double passe :
      Passe 1 : A/B/C/D par lots de 6 pages (system prompt × nb lots + texte)
      Passe 2 : E/F/G sur document entier (1 appel avec texte complet)
    """
    text_chars = len(extraction.full_text)
    text_tokens = text_chars // 4

    # Passe 1
    system_p1_tokens = 2800
    batches = max(1, extraction.total_pages // 6)
    input_p1 = system_p1_tokens * batches + text_tokens

    # Passe 2 — 1 appel avec texte entier (plafonné à 22K tokens)
    system_p2_tokens = 2000
    full_text_tokens = min(text_tokens, 22_000)
    input_p2 = system_p2_tokens + full_text_tokens

    input_tokens = input_p1 + input_p2

    # Output : ~2.5 corrections/page — volontairement surestimé pour que l'estimation
    # affichée dépasse toujours le coût réel (sécurité budgétaire pour l'éditeur).
    estimated_corrections = max(1, int(extraction.total_pages * 2.5))
    output_tokens = int(estimated_corrections * 80 * 1.3)

    total_cost = (
        (input_tokens / 1_000_000) * input_price
        + (output_tokens / 1_000_000) * output_price
    )

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_tokens": input_tokens + output_tokens,
        "estimated_cost_usd": round(total_cost, 4),
        "estimated_corrections": estimated_corrections,
    }
