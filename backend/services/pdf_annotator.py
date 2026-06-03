"""
Native PDF annotation using PyMuPDF.
Adds StrikeOut, Highlight, and Squiggly annotations with colored overlays
and structured comment popups — visible in Adobe Acrobat, macOS Preview, etc.

Text matching uses a multi-strategy approach for maximum robustness.
Confidence is displayed as a picto : ✅ Certain / ⚠️ Probable / ❓ À vérifier

A summary page is appended at the end of the annotated PDF.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

CATEGORY_NAMES: dict[str, str] = {
    "A": "A - Orthographe",
    "B": "B - Grammaire",
    "C": "C - Typographie",
    "D": "D - Syntaxe & Style",
    "E": "E - Semantique",
    "F": "F - Uniformisation",
    "G": "G - Renvois",
    "H": "H - Verification des faits",
}

CATEGORY_CONFIG: dict[str, dict] = {
    "A": {"color": (1.00, 0.65, 0.65), "type": "StrikeOut"},
    "B": {"color": (1.00, 0.82, 0.55), "type": "StrikeOut"},
    "C": {"color": (0.78, 0.60, 1.00), "type": "Highlight"},
    "D": {"color": (0.55, 0.78, 1.00), "type": "Highlight"},
    "E": {"color": (0.60, 0.90, 0.65), "type": "Highlight"},
    "F": {"color": (0.55, 0.90, 0.95), "type": "Squiggly"},
    "G": {"color": (1.00, 0.72, 0.87), "type": "Squiggly"},
    "H": {"color": (1.00, 0.92, 0.40), "type": "Highlight"},
}

# Pictos de confiance — Option B : ✅ ⚠️ ❓
CONFIDENCE_PICTO: dict[str, str] = {
    "Certain":    "✅",
    "Probable":   "⚠️",
    "À vérifier": "❓",
}

_NORM_PAIRS: list[tuple[str, str]] = [
    ("\u2019", "'"),
    ("\u2018", "'"),
    ("\u2032", "'"),
    ("\u201c", '"'),
    ("\u201d", '"'),
    ("\u00e6", "ae"),
    ("\u0153", "oe"),
    ("\u00c6", "AE"),
    ("\u0152", "OE"),
    ("\u2014", "—"),
    ("\u2013", "–"),
    ("\u00a0", " "),
    ("\u202f", " "),
    ("\u2026", "..."),
    ("\u00ab", "«"),
    ("\u00bb", "»"),
]


def _normalize(text: str) -> str:
    for src, dst in _NORM_PAIRS:
        text = text.replace(src, dst)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _normalize_for_search(text: str) -> str:
    normalized = _normalize(text)
    normalized = normalized.replace("\u2019", "'").replace("\u2018", "'")
    return normalized


def _find_by_words(page: fitz.Page, text: str) -> list[fitz.Rect]:
    """
    Recherche les mots dans la page et retourne un rect **par ligne** du match.
    Évite le rect fusionné multi-lignes qui génère un highlight énorme et opaque.
    """
    try:
        words_raw = page.get_text("words")
    except Exception:
        return []
    if not words_raw:
        return []
    search_parts = text.split()
    if not search_parts:
        return []
    n = len(search_parts)
    total_words = len(words_raw)
    if total_words < n:
        return []
    page_words_norm = [_normalize(w[4]).lower() for w in words_raw]
    search_norm = [_normalize(sw).lower() for sw in search_parts]
    results: list[fitz.Rect] = []
    for i in range(total_words - n + 1):
        if all(page_words_norm[i + j] == search_norm[j] for j in range(n)):
            # Regrouper les mots par ligne (même y0 arrondi) → un rect par ligne
            line_groups: dict[int, list] = {}
            for k in range(n):
                w = words_raw[i + k]
                line_key = round(w[1])  # y0 arrondi à l'entier
                if line_key not in line_groups:
                    line_groups[line_key] = []
                line_groups[line_key].append(w)
            for group in sorted(line_groups.values(), key=lambda g: g[0][1]):
                x0 = min(w[0] for w in group)
                y0 = min(w[1] for w in group)
                x1 = max(w[2] for w in group)
                y1 = max(w[3] for w in group)
                results.append(fitz.Rect(x0, y0, x1, y1))
            break  # premier match suffit
    return results


def _find_by_words_fuzzy(page: fitz.Page, text: str) -> list[fitz.Rect]:
    try:
        words_raw = page.get_text("words")
    except Exception:
        return []
    if not words_raw or len(text.split()) != 1:
        return []
    search_norm = _normalize(text).lower()
    return [
        fitz.Rect(w[0], w[1], w[2], w[3])
        for w in words_raw
        if _normalize(w[4]).lower() == search_norm
    ]


def _find_text_rects(page: fitz.Page, text: str) -> list[fitz.Rect]:
    if not text or not text.strip():
        return []
    text = text.strip()

    rects = page.search_for(text)
    if rects:
        return rects

    normalized = _normalize_for_search(text)
    if normalized != text:
        rects = page.search_for(normalized)
        if rects:
            return rects

    rects = _find_by_words(page, text)
    if rects:
        return rects

    if normalized != text:
        rects = _find_by_words(page, normalized)
        if rects:
            return rects

    rects = _find_by_words_fuzzy(page, text)
    if rects:
        return rects[:1]

    if len(text) > 30:
        fragment = text[:30].rstrip()
        rects = page.search_for(fragment)
        if rects:
            return rects[:1]
        frag_norm = _normalize_for_search(fragment)
        if frag_norm != fragment:
            rects = page.search_for(frag_norm)
            if rects:
                return rects[:1]

    return []


CATEGORY_UPPER: dict[str, str] = {
    "A": "ORTHOGRAPHE",
    "B": "GRAMMAIRE",
    "C": "TYPOGRAPHIE",
    "D": "SYNTAXE & STYLE",
    "E": "SÉMANTIQUE",
    "F": "UNIFORMISATION",
    "G": "RENVOIS",
    "H": "VÉRIFICATION DES FAITS",
}


def _build_comment(req: "AnnotationRequest", mode: str = "detailed") -> str:
    """
    Formate le texte de l'annotation PDF.

    mode="simple"   → format compact : type d'erreur + correction directe.
    mode="detailed" → format complet : type + confiance + explication + correction + source.

    Catégorie H : toujours présentée comme une "vérification suggérée" (jamais une erreur
    certaine), quel que soit le mode — l'éditeur humain valide en dernier ressort.
    """
    # Catégorie H : wording spécifique pour distinguer les suggestions des erreurs certaines
    if req.category == "H":
        corrected_display = f"« {req.corrected_text} »" if req.corrected_text else "à confirmer"
        if mode == "simple":
            return (
                f"⚠️ VÉRIFICATION SUGGÉRÉE\n"
                f"{req.description}\n"
                f"Forme possible : {corrected_display}"
            )
        lines = [
            "⚠️ VÉRIFICATION SUGGÉRÉE PAR L'IA",
            "",
            f"Motif : {req.description}",
            "",
            f"Explication : {req.explanation}",
            "",
            f"Forme possible : {corrected_display}",
            "",
            "→ À valider par l'éditeur ou l'auteur avant toute correction.",
        ]
        if req.source:
            lines += ["", f"Source consultée : {req.source}"]
        return "\n".join(lines)

    # Catégories A-G : format standard
    cat_label = CATEGORY_UPPER.get(req.category, req.category)
    corrected_display = (
        f"« {req.corrected_text} »" if req.corrected_text else "—"
    )

    if mode == "simple":
        return f"{cat_label} → {req.description}\nCorrection : {corrected_display}"

    # Mode détaillé (défaut)
    picto = CONFIDENCE_PICTO.get(req.confidence, "⚠️")
    lines = [
        f"{cat_label} → {req.description}",
        f"{picto} {req.confidence}",
        "",
        f"Explication : {req.explanation}",
        "",
        f"Correction proposée : {corrected_display}",
    ]
    if req.source:
        lines += ["", req.source]

    return "\n".join(lines)


def _add_annotation(
    page: fitz.Page,
    rects: list[fitz.Rect],
    category: str,
    title: str,
    comment: str,
) -> bool:
    config = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG["D"])
    color = config["color"]
    annot_type = config["type"]
    try:
        if annot_type == "StrikeOut":
            annot = page.add_strikeout_annot(rects)
        elif annot_type == "Squiggly":
            annot = page.add_squiggly_annot(rects)
        else:
            annot = page.add_highlight_annot(rects)
        annot.set_colors(stroke=color)
        annot.set_info(title=title, content=comment)
        # Opacité à 0.6 : texte toujours lisible sous le highlight
        annot.update(opacity=0.6)
        return True
    except Exception as exc:
        logger.warning("Annotation échouée (%s) : %s", annot_type, exc)
        return False


@dataclass
class AnnotationRequest:
    page_num: int
    category: str
    original_text: str
    corrected_text: str | None
    description: str
    explanation: str
    source: str
    confidence: str = field(default="Probable")


_DOC_TYPE_LABELS: dict[str, str] = {
    "roman":          "Roman / Fiction",
    "bd_comics":      "BD / Comics / Manga",
    "jeunesse":       "Jeunesse / Albums",
    "poesie_theatre": "Poésie / Théâtre",
    "documentaire":   "Documentaire / Sciences",
    "tourisme":       "Tourisme / Voyages",
    "cuisine":        "Cuisine / Gastronomie",
    "sport":          "Sport / Bien-être",
    "manuel_scolaire":"Manuel scolaire",
    "parascolaire":   "Parascolaire",
    "essai":          "Essai / Rapport",
    "magazine":       "Magazine / Presse",
    "revue_presse":   "Revue / Journal",
    "entretien":      "Entretien / Interview",
    "autre":          "Autre document",
}

_PRESET_LABELS: dict[str, str] = {
    "correction-complete": "Correction complete (A-H)",
    "correction-rapide":   "Correction rapide (A-D)",
    "coherence-globale":   "Coherence globale (E-G)",
    "verification-faits":  "Verification des faits (H)",
}

# Characters outside the Helvetica basique subset — cause artefacts in PyMuPDF
_SUMMARY_REPLACEMENTS: list[tuple[str, str]] = [
    ("\u2026", "..."),   # ellipsis typographique → trois points
    ("\u2019", "'"),     # apostrophe typographique → droite
    ("\u2018", "'"),
    ("\u201c", '"'),     # guillemets doubles
    ("\u201d", '"'),
    ("\u00ab", "<<"),    # guillemets français
    ("\u00bb", ">>"),
    ("\u2014", "--"),    # tiret cadratin
    ("\u2013", "-"),     # demi-cadratin
    ("\u00b7", "."),     # point médian
    ("\u00a0", " "),     # espace insécable
    ("\u202f", " "),     # espace fine insécable
    ("\u00e6", "ae"),    # æ
    ("\u0153", "oe"),    # œ
    ("\u00c6", "AE"),
    ("\u0152", "OE"),
    ("\u00e9", "e"),     # é, è, ê, ë → e  (Helvetica de base ne les a pas tous)
    ("\u00e8", "e"),
    ("\u00ea", "e"),
    ("\u00eb", "e"),
    ("\u00e0", "a"),
    ("\u00e2", "a"),
    ("\u00f4", "o"),
    ("\u00fb", "u"),
    ("\u00f9", "u"),
    ("\u00ee", "i"),
    ("\u00ef", "i"),
    ("\u00e7", "c"),
    ("\u00c9", "E"),
    ("\u00c8", "E"),
    ("\u00ca", "E"),
    ("\u00c0", "A"),
]


def _safe_summary_text(text: str, max_len: int = 35) -> str:
    """Nettoie le texte pour insertion dans la page de synthèse PyMuPDF.

    - Supprime les sauts de ligne (causent un décalage de ligne)
    - Remplace les caractères hors Helvetica basique par des équivalents ASCII
    - Tronque proprement
    """
    cleaned = text.replace("\n", " ").replace("\r", " ")
    for src, dst in _SUMMARY_REPLACEMENTS:
        cleaned = cleaned.replace(src, dst)
    # Supprimer tout caractère non-ASCII restant qui pourrait causer des artefacts
    cleaned = "".join(c if ord(c) < 128 else "?" for c in cleaned)
    cleaned = re.sub(r" {2,}", " ", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len] + "..."
    return cleaned


def _append_summary_page(
    doc: fitz.Document,
    annotations: list[AnnotationRequest],
    by_category_annotated: dict[str, int],
    h_not_annotated: list[AnnotationRequest] | None = None,
    doc_type: str = "autre",
    preset_label: str = "correction",
) -> None:
    """
    Ajoute une page de synthèse en fin de document.
    Liste toutes les corrections par catégorie avec page, texte original et correction.
    Format lisible pour les chargés d'édition.
    """
    page = doc.new_page(width=595, height=842)  # A4 portrait

    # Couleurs de fond légères par catégorie (RGB 0-1)
    CAT_FILL = {
        "A": (1.00, 0.90, 0.90),
        "B": (1.00, 0.95, 0.85),
        "C": (0.92, 0.88, 1.00),
        "D": (0.88, 0.93, 1.00),
        "E": (0.88, 0.97, 0.88),
        "F": (0.88, 0.97, 0.99),
        "G": (1.00, 0.93, 0.96),
        "H": (1.00, 0.98, 0.82),
    }

    margin_x = 40
    y = 40
    line_h = 14
    col_page = margin_x
    col_orig = margin_x + 35
    col_corr = margin_x + 210
    col_conf = margin_x + 385
    page_width = 595 - margin_x

    # ── En-tête ────────────────────────────────────────────────────────────────
    page.insert_text(
        (margin_x, y), "RAPPORT DE CORRECTION ÉDITORIALE",
        fontsize=13, fontname="Helvetica-Bold", color=(0.1, 0.1, 0.1),
    )
    y += 20

    total = sum(by_category_annotated.values())
    page.insert_text(
        (margin_x, y),
        f"{total} correction{'s' if total > 1 else ''} annotée{'s' if total > 1 else ''}",
        fontsize=9, fontname="Helvetica", color=(0.4, 0.4, 0.4),
    )
    y += 14

    doc_type_str  = _DOC_TYPE_LABELS.get(doc_type, doc_type)
    preset_str    = _PRESET_LABELS.get(preset_label, preset_label)
    page.insert_text(
        (margin_x, y),
        f"Type de document : {doc_type_str}   |   Mode : {preset_str}",
        fontsize=8, fontname="Helvetica-Oblique", color=(0.5, 0.5, 0.5),
    )
    y += 14

    # ── Tableau de synthèse ────────────────────────────────────────────────────
    page.draw_line((margin_x, y), (555, y), color=(0.7, 0.7, 0.7), width=0.5)
    y += 8

    for cat in sorted(by_category_annotated.keys()):
        count = by_category_annotated.get(cat, 0)
        if count == 0:
            continue
        fill = CAT_FILL.get(cat, (1, 1, 1))
        page.draw_rect(
            fitz.Rect(margin_x, y - 10, 555, y + 4),
            color=None, fill=fill,
        )
        cat_name = CATEGORY_NAMES.get(cat, cat)
        page.insert_text(
            (margin_x + 4, y),
            f"{cat_name}",
            fontsize=9, fontname="Helvetica-Bold", color=(0.15, 0.15, 0.15),
        )
        page.insert_text(
            (440, y),
            f"{count} correction{'s' if count > 1 else ''}",
            fontsize=9, fontname="Helvetica", color=(0.3, 0.3, 0.3),
        )
        y += line_h

    y += 12
    page.draw_line((margin_x, y), (555, y), color=(0.7, 0.7, 0.7), width=0.5)
    y += 14

    # ── Détail par catégorie ───────────────────────────────────────────────────
    # Regrouper les annotations par catégorie puis par page
    by_cat: dict[str, list[AnnotationRequest]] = {}
    for req in annotations:
        by_cat.setdefault(req.category, []).append(req)

    for cat in sorted(by_cat.keys()):
        if y > 790:
            page.insert_text(
                (margin_x, y),
                "… Rapport tronqué — consulter le PDF annoté pour les corrections restantes.",
                fontsize=7, fontname="Helvetica-Oblique", color=(0.5, 0.5, 0.5),
            )
            break  # plus de place sur cette page — synthèse tronquée

        reqs = sorted(by_cat[cat], key=lambda r: r.page_num)
        fill = CAT_FILL.get(cat, (1, 1, 1))
        cat_name = CATEGORY_NAMES.get(cat, cat)

        # Titre de section
        page.draw_rect(
            fitz.Rect(margin_x, y - 11, 555, y + 3),
            color=None, fill=fill,
        )
        page.insert_text(
            (margin_x + 4, y),
            cat_name.upper(),
            fontsize=8, fontname="Helvetica-Bold", color=(0.1, 0.1, 0.1),
        )
        y += line_h

        # En-tête colonnes
        page.insert_text((col_page, y), "Pg.", fontsize=7, fontname="Helvetica-BoldOblique", color=(0.5, 0.5, 0.5))
        page.insert_text((col_orig, y), "Texte original", fontsize=7, fontname="Helvetica-BoldOblique", color=(0.5, 0.5, 0.5))
        page.insert_text((col_corr, y), "Correction proposée", fontsize=7, fontname="Helvetica-BoldOblique", color=(0.5, 0.5, 0.5))
        page.insert_text((col_conf, y), "Conf.", fontsize=7, fontname="Helvetica-BoldOblique", color=(0.5, 0.5, 0.5))
        y += 10
        page.draw_line((margin_x, y), (555, y), color=(0.85, 0.85, 0.85), width=0.3)
        y += 6

        for req in reqs:
            if y > 810:
                page.insert_text(
                    (margin_x, y), f"… {len(reqs)} corrections au total (voir PDF annoté)",
                    fontsize=7, fontname="Helvetica-Oblique", color=(0.5, 0.5, 0.5),
                )
                y += line_h
                break

            orig  = _safe_summary_text(req.original_text, 35)
            corr  = _safe_summary_text(req.corrected_text or "-", 35)
            picto_str = {"Certain": "[C]", "Probable": "[P]", "A verifier": "[?]"}.get(
                req.confidence, "[P]"
            )

            page.insert_text((col_page, y), str(req.page_num + 1), fontsize=7.5, fontname="Helvetica", color=(0.3, 0.3, 0.3))
            page.insert_text((col_orig, y), orig, fontsize=7.5, fontname="Helvetica", color=(0.15, 0.15, 0.15))
            page.insert_text((col_corr, y), corr, fontsize=7.5, fontname="Helvetica", color=(0.15, 0.15, 0.15))
            page.insert_text((col_conf, y), picto_str, fontsize=7.5, fontname="Helvetica", color=(0.3, 0.3, 0.3))
            y += line_h

        y += 6

    # ── H non localisées (texte dans image rasterisée) ────────────────────────
    if h_not_annotated and y < 800:
        y += 8
        page.draw_line((margin_x, y), (555, y), color=(0.7, 0.7, 0.7), width=0.5)
        y += 14

        fill_h = (1.00, 0.98, 0.82)
        page.draw_rect(
            fitz.Rect(margin_x, y - 11, 555, y + 3),
            color=None, fill=fill_h,
        )
        page.insert_text(
            (margin_x + 4, y),
            "H - VERIFICATIONS SUGGEREES NON LOCALISEES DANS LE PDF",
            fontsize=8, fontname="Helvetica-Bold", color=(0.65, 0.42, 0.00),
        )
        y += line_h

        page.insert_text(
            (margin_x + 4, y),
            "[A valider par l'editeur - texte non localise dans le PDF - verification manuelle requise]",
            fontsize=7, fontname="Helvetica-Oblique", color=(0.55, 0.45, 0.10),
        )
        y += 10
        page.draw_line((margin_x, y), (555, y), color=(0.85, 0.85, 0.85), width=0.3)
        y += 6

        for req in h_not_annotated:
            if y > 810:
                page.insert_text(
                    (margin_x, y),
                    f"... {len(h_not_annotated)} elements au total (voir rapport Word)",
                    fontsize=7, fontname="Helvetica-Oblique", color=(0.5, 0.5, 0.5),
                )
                break
            orig  = _safe_summary_text(req.original_text, 35)
            corr  = _safe_summary_text(req.corrected_text or "-", 35)
            picto = {"Certain": "[C]", "Probable": "[P]", "A verifier": "[?]"}.get(req.confidence, "[P]")

            page.insert_text(
                (col_page, y), str(req.page_num + 1),
                fontsize=7.5, fontname="Helvetica", color=(0.3, 0.3, 0.3),
            )
            page.insert_text(
                (col_orig, y), orig,
                fontsize=7.5, fontname="Helvetica", color=(0.6, 0.40, 0.0),
            )
            page.insert_text(
                (col_corr, y), corr,
                fontsize=7.5, fontname="Helvetica", color=(0.15, 0.15, 0.15),
            )
            page.insert_text(
                (col_conf, y), picto,
                fontsize=7.5, fontname="Helvetica", color=(0.5, 0.3, 0.0),
            )
            y += line_h

    # ── Pied de page ───────────────────────────────────────────────────────────
    page.draw_line((margin_x, 820), (555, 820), color=(0.8, 0.8, 0.8), width=0.3)
    page.insert_text(
        (margin_x, 832),
        "Genere par EditorIA - Rapport non contractuel, a valider par le correcteur.",
        fontsize=7, fontname="Helvetica-Oblique", color=(0.6, 0.6, 0.6),
    )


def annotate_pdf(
    input_path: str | Path,
    output_path: str | Path,
    annotations: list[AnnotationRequest],
    doc_type: str = "autre",
    preset_label: str = "correction",
    comment_mode: str = "detailed",
) -> dict[str, Any]:
    """
    Annotate a PDF with editorial corrections and append a summary page.

    Returns annotation statistics dict. Raises on unrecoverable PDF errors.
    The fitz.Document is always closed even if annotation fails.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    doc = fitz.open(str(input_path))
    total_pages = len(doc)
    # Guard: ensure doc is closed even on unexpected exceptions
    try:
        return _annotate_pdf_impl(doc, input_path, output_path, annotations, total_pages, doc_type, preset_label, comment_mode)
    except Exception:
        doc.close()
        raise


def _annotate_pdf_impl(
    doc: "fitz.Document",
    input_path: Path,
    output_path: Path,
    annotations: list[AnnotationRequest],
    total_pages: int,
    doc_type: str = "autre",
    preset_label: str = "correction",
    comment_mode: str = "detailed",
) -> dict[str, Any]:

    stats: dict[str, int] = {cat: 0 for cat in CATEGORY_CONFIG}
    not_found: list[str] = []
    annotated_total = 0
    annotated_requests: list[AnnotationRequest] = []
    h_not_annotated: list[AnnotationRequest] = []

    by_page: dict[int, list[AnnotationRequest]] = {}
    for req in annotations:
        page_num = min(max(req.page_num, 0), total_pages - 1)
        by_page.setdefault(page_num, []).append(req)

    # Pre-compute a narrow search window for fallbacks: only scan ±5 pages around
    # the expected page to avoid O(corrections × pages) scans on long PDFs.
    FALLBACK_WINDOW = 5

    for page_num in sorted(by_page.keys()):
        page = doc[page_num]
        page_ok = 0
        page_miss = 0

        for req in by_page[page_num]:
            rects = _find_text_rects(page, req.original_text)

            if not rects:
                # Fallback : cherche dans une fenêtre étroite (±FALLBACK_WINDOW pages)
                # puis dans tout le document si toujours absent.
                fallback_page_num: int | None = None
                fallback_rects: list[fitz.Rect] | None = None

                # Fenêtre étroite d'abord
                window_start = max(0, page_num - FALLBACK_WINDOW)
                window_end = min(total_pages, page_num + FALLBACK_WINDOW + 1)
                search_order = list(range(window_start, window_end))
                # Puis le reste du document si nécessaire
                search_order += [i for i in range(total_pages)
                                  if i < window_start or i >= window_end]

                for fb_idx in search_order:
                    if fb_idx == page_num:
                        continue
                    fb_rects = _find_text_rects(doc[fb_idx], req.original_text)
                    if fb_rects:
                        fallback_page_num = fb_idx
                        fallback_rects = fb_rects
                        break

                if fallback_rects is not None and fallback_page_num is not None:
                    logger.info(
                        "Fallback p.%d→p.%d [%s] : %s",
                        page_num + 1, fallback_page_num + 1, req.category, req.original_text[:50],
                    )
                    picto = CONFIDENCE_PICTO.get(req.confidence, "⚠️")
                    title = f"{picto} {req.confidence}"
                    comment = _build_comment(req, mode=comment_mode)
                    if _add_annotation(doc[fallback_page_num], fallback_rects, req.category, title, comment):
                        stats[req.category] = stats.get(req.category, 0) + 1
                        annotated_total += 1
                        annotated_requests.append(AnnotationRequest(
                            page_num=fallback_page_num,
                            category=req.category,
                            original_text=req.original_text,
                            corrected_text=req.corrected_text,
                            description=req.description,
                            explanation=req.explanation,
                            source=req.source,
                            confidence=req.confidence,
                        ))
                        page_ok += 1
                else:
                    not_found.append(f"p.{page_num + 1}[{req.category}]: {req.original_text[:60]}")
                    page_miss += 1
                    if req.category == "H":
                        h_not_annotated.append(req)
                continue

            picto = CONFIDENCE_PICTO.get(req.confidence, "⚠️")
            title = f"{picto} {req.confidence}"
            comment = _build_comment(req, mode=comment_mode)
            if _add_annotation(page, rects, req.category, title, comment):
                stats[req.category] = stats.get(req.category, 0) + 1
                annotated_total += 1
                annotated_requests.append(req)
                page_ok += 1

        logger.debug("Page %d : %d annotées, %d non trouvées", page_num + 1, page_ok, page_miss)

    # ── Page de synthèse en fin de document ───────────────────────────────────
    if annotated_requests or h_not_annotated:
        _append_summary_page(doc, annotated_requests, stats, h_not_annotated, doc_type, preset_label)

    doc.save(str(output_path), garbage=4, deflate=True)
    doc.close()  # Always closed here in the happy path; wrapper closes on exception

    # Alerte si taux d'échec élevé
    total_attempted = annotated_total + len(not_found)
    if total_attempted > 0:
        failure_rate = len(not_found) / total_attempted
        if failure_rate > 0.15:
            logger.warning(
                "⚠️ Taux d'échec annotation élevé : %.0f%% (%d/%d non trouvés)",
                failure_rate * 100, len(not_found), total_attempted,
            )

    logger.info(
        "Annotation terminée : %d annotées, %d non trouvées | par catégorie : %s",
        annotated_total, len(not_found),
        {k: v for k, v in stats.items() if v > 0},
    )

    return {
        "annotated": annotated_total,
        "not_found": len(not_found),
        "not_found_samples": not_found[:30],
        "by_category": stats,
        "failure_rate": round(len(not_found) / max(total_attempted, 1), 3),
        "h_not_annotated": len(h_not_annotated),
        "h_not_annotated_texts": [r.original_text[:80] for r in h_not_annotated[:10]],
    }
