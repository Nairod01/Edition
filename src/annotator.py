"""
Injection des annotations éditoriales dans le PDF.
Utilise PyMuPDF pour créer des annotations natives Adobe Acrobat.

Types d'annotations utilisées :
- Highlight + note attachée  → problème localisé sur un mot/passage
- Text (sticky note)         → remarque générale sur une page
- Strikeout + note           → suggestion de suppression
- FreeText                   → correction en ligne (visible sans clic)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

from .analyzer import EditorialIssue

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Configuration des annotations par catégorie
# ─────────────────────────────────────────────

@dataclass
class AnnotationStyle:
    highlight_color: tuple[float, float, float]
    icon_color: tuple[float, float, float]
    note_icon: str  # "Comment" | "Note" | "Key" | "Help" | "Insert" | "Paragraph"
    prefix: str     # préfixe du message pour le filtrage dans Adobe


ANNOTATION_STYLES: dict[str, AnnotationStyle] = {
    "orthographe": AnnotationStyle(
        highlight_color=(1.0, 0.3, 0.3),
        icon_color=(0.85, 0.05, 0.05),
        note_icon="Note",
        prefix="[ORTHO]",
    ),
    "typographie": AnnotationStyle(
        highlight_color=(1.0, 0.65, 0.1),
        icon_color=(0.9, 0.45, 0.0),
        note_icon="Comment",
        prefix="[TYPO]",
    ),
    "style": AnnotationStyle(
        highlight_color=(1.0, 0.95, 0.2),
        icon_color=(0.8, 0.7, 0.0),
        note_icon="Paragraph",
        prefix="[STYLE]",
    ),
    "homogeneisation": AnnotationStyle(
        highlight_color=(0.4, 0.7, 1.0),
        icon_color=(0.1, 0.4, 0.85),
        note_icon="Insert",
        prefix="[COHÉR.]",
    ),
    "structure": AnnotationStyle(
        highlight_color=(0.75, 0.4, 1.0),
        icon_color=(0.5, 0.1, 0.75),
        note_icon="Help",
        prefix="[STRUCT.]",
    ),
    "maquette": AnnotationStyle(
        highlight_color=(0.3, 0.85, 0.4),
        icon_color=(0.1, 0.65, 0.25),
        note_icon="Key",
        prefix="[MAQUETTE]",
    ),
}

_DEFAULT_STYLE = AnnotationStyle(
    highlight_color=(0.8, 0.8, 0.8),
    icon_color=(0.5, 0.5, 0.5),
    note_icon="Comment",
    prefix="[NOTE]",
)


# ─────────────────────────────────────────────
# Statistiques d'annotation
# ─────────────────────────────────────────────

@dataclass
class AnnotationStats:
    total: int = 0
    placed: int = 0        # annotation positionnée précisément sur le texte
    fallback: int = 0      # sticky note en marge (texte non trouvé)
    skipped: int = 0       # non traitées (page hors limites, etc.)
    by_category: dict = None

    def __post_init__(self):
        if self.by_category is None:
            self.by_category = {}

    def record(self, category: str, placed: bool):
        self.total += 1
        if placed:
            self.placed += 1
        else:
            self.fallback += 1
        self.by_category[category] = self.by_category.get(category, 0) + 1


# ─────────────────────────────────────────────
# Annotateur principal
# ─────────────────────────────────────────────

class PDFAnnotator:
    """
    Injecte les issues éditoriales dans un PDF sous forme
    d'annotations natives Adobe Acrobat.
    """

    # Nombre de pages à scanner au-delà de la page déclarée pour trouver un snippet
    PAGE_SEARCH_RADIUS = 5

    # Auteur des annotations
    ANNOTATION_AUTHOR = "Éditrice — Outil IA"

    def __init__(self, input_path: str | Path, output_path: str | Path):
        self.input_path = Path(input_path)
        self.output_path = Path(output_path)
        self._doc: fitz.Document = fitz.open(str(self.input_path))
        self.stats = AnnotationStats()

    def annotate(self, issues: list[EditorialIssue]) -> AnnotationStats:
        """
        Injecte toutes les issues dans le document.
        Retourne les statistiques d'annotation.
        """
        # Trier par page pour un traitement séquentiel plus efficace
        sorted_issues = sorted(issues, key=lambda i: (i.page_num, i.category))

        for issue in sorted_issues:
            self._place_annotation(issue)

        return self.stats

    def save(self):
        """Sauvegarde le document annoté."""
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        # garbage=3 et deflate=True pour compresser proprement
        self._doc.save(
            str(self.output_path),
            garbage=3,
            deflate=True,
            incremental=False,
        )
        logger.info(f"PDF annoté sauvegardé : {self.output_path}")

    def close(self):
        self._doc.close()

    # ── Placement d'une annotation ───────────

    def _place_annotation(self, issue: EditorialIssue):
        """Tente de placer l'annotation à la position exacte du snippet."""
        style = ANNOTATION_STYLES.get(issue.category, _DEFAULT_STYLE)
        page_idx = issue.page_num - 1  # 0-indexé

        if page_idx < 0 or page_idx >= len(self._doc):
            self.stats.skipped += 1
            return

        # Cherche le snippet sur la page déclarée + pages voisines
        found_page, quads = self._find_text(issue.text_snippet, page_idx)

        if found_page is not None and quads:
            self._add_highlight_annotation(found_page, quads, issue, style)
            self.stats.record(issue.category, placed=True)
        else:
            # Fallback : sticky note positionnée en marge
            page = self._doc[page_idx]
            self._add_sticky_note(page, issue, style)
            self.stats.record(issue.category, placed=False)

    # ── Recherche du texte ───────────────────

    def _find_text(self, snippet: str, page_idx: int) -> tuple[Optional[fitz.Page], Optional[list]]:
        """
        Cherche le snippet sur la page cible et les pages voisines.
        Retourne (page, quads) ou (None, None).
        """
        if not snippet or not snippet.strip():
            return None, None

        search_text = self._normalize_for_search(snippet)
        page_count = len(self._doc)

        # Cherche d'abord sur la page déclarée, puis sur les pages suivantes et précédentes
        pages_to_try = [page_idx]
        for delta in range(1, self.PAGE_SEARCH_RADIUS + 1):
            if page_idx + delta < page_count:
                pages_to_try.append(page_idx + delta)
            if page_idx - delta >= 0:
                pages_to_try.append(page_idx - delta)

        for idx in pages_to_try:
            page = self._doc[idx]
            # search_for retourne une liste de Rect
            rects = page.search_for(search_text, quads=True)
            if rects:
                return page, rects

            # Second essai avec le début du snippet (premiers 30 caractères)
            if len(search_text) > 30:
                rects = page.search_for(search_text[:30].strip(), quads=True)
                if rects:
                    return page, [rects[0]]  # on prend le premier résultat

        return None, None

    def _normalize_for_search(self, text: str) -> str:
        """Normalise le texte pour la recherche dans le PDF."""
        import unicodedata
        # Retire les retours à la ligne internes, normalise les espaces
        text = " ".join(text.split())
        # Remplace les espaces insécables par des espaces normaux pour la recherche
        text = text.replace("\u00a0", " ").replace("\u202f", " ")
        # Limite à 60 caractères pour éviter les snippets trop longs jamais trouvés
        if len(text) > 60:
            # Prend les premiers mots entiers dans 60 caractères
            text = text[:60].rsplit(" ", 1)[0]
        return text.strip()

    # ── Création des annotations ─────────────

    def _add_highlight_annotation(
        self,
        page: fitz.Page,
        quads: list,
        issue: EditorialIssue,
        style: AnnotationStyle,
    ):
        """Crée un surlignage coloré avec une note attachée."""
        content = self._format_annotation_text(issue, style)

        # Surlignage du texte fautif
        annot = page.add_highlight_annot(quads)
        annot.set_colors(stroke=style.highlight_color)
        annot.set_info(
            title=self.ANNOTATION_AUTHOR,
            content=content,
            subject=f"{style.prefix} {issue.category.title()}",
        )
        annot.set_opacity(0.6)
        annot.update()

        # Si correction proposée : ajouter aussi une note positionnée sur le premier quad
        if issue.correction and issue.correction != issue.text_snippet:
            first_rect = fitz.Quad(quads[0]).rect if hasattr(quads[0], "rect") else fitz.Rect(quads[0])
            # Note compacte avec la correction
            note_point = fitz.Point(first_rect.x1 + 5, first_rect.y0)
            note_annot = page.add_text_annot(note_point, f"→ {issue.correction}")
            note_annot.set_info(
                title=self.ANNOTATION_AUTHOR,
                subject=f"Correction {style.prefix}",
            )
            note_annot.set_colors(
                stroke=style.icon_color,
                fill=style.highlight_color,
            )
            note_annot.update()

    def _add_sticky_note(self, page: fitz.Page, issue: EditorialIssue, style: AnnotationStyle):
        """Sticky note en marge (fallback quand le texte n'est pas trouvé)."""
        content = self._format_annotation_text(issue, style)

        # Positionnement en marge droite, réparti verticalement selon l'index
        margin_x = page.rect.width - 30
        margin_y = 50 + (hash(issue.text_snippet) % int(page.rect.height - 100))
        point = fitz.Point(margin_x, margin_y)

        annot = page.add_text_annot(point, content, icon=style.note_icon)
        annot.set_info(
            title=self.ANNOTATION_AUTHOR,
            content=content,
            subject=f"{style.prefix} — Passage : «\u00a0{issue.text_snippet[:40]}\u00a0»",
        )
        annot.set_colors(
            stroke=style.icon_color,
            fill=tuple(min(1.0, c + 0.3) for c in style.icon_color),
        )
        annot.update()

    # ── Formatage du texte des annotations ───

    def _format_annotation_text(self, issue: EditorialIssue, style: AnnotationStyle) -> str:
        """Construit le texte complet de l'annotation au format professionnel."""
        lines = [
            f"{style.prefix} {issue.message}",
        ]

        if issue.correction and issue.correction.strip():
            lines.append(f"\nSuggestion : {issue.correction}")

        if issue.rule_id:
            lines.append(f"\n[Règle : {issue.rule_id}]")

        return "\n".join(lines)

    # ── Légende ─────────────────────────────

    def add_legend_page(self):
        """Insère une page de légende des annotations au début du document."""
        page = self._doc.new_page(0, width=595, height=842)  # A4

        # Titre
        page.insert_text(
            fitz.Point(50, 60),
            "RAPPORT D'ANNOTATIONS ÉDITORIALES",
            fontsize=16,
            fontname="helv",
            color=(0.1, 0.1, 0.1),
        )
        page.insert_text(
            fitz.Point(50, 85),
            f"Document analysé par l'outil d'édition IA — {self.ANNOTATION_AUTHOR}",
            fontsize=10,
            color=(0.4, 0.4, 0.4),
        )

        y = 130
        page.insert_text(fitz.Point(50, y), "CODE COULEUR DES ANNOTATIONS :", fontsize=11,
                         fontname="helv", color=(0.1, 0.1, 0.1))
        y += 25

        legend_items = [
            ("orthographe",     "Rouge",    "Fautes d'orthographe et de grammaire"),
            ("typographie",     "Orange",   "Problèmes typographiques (espaces, guillemets, tirets…)"),
            ("style",           "Jaune",    "Style et lisibilité (répétitions, lourdeurs, clichés…)"),
            ("homogeneisation", "Bleu",     "Cohérence et homogénéisation (noms, temps, dialogue…)"),
            ("structure",       "Violet",   "Structure narrative (chapitres, transitions, POV…)"),
            ("maquette",        "Vert",     "Mise en page et formatage"),
        ]

        for cat, color_label, description in legend_items:
            style = ANNOTATION_STYLES[cat]
            rgb = style.highlight_color
            # Rectangle de couleur
            rect = fitz.Rect(50, y - 10, 75, y + 5)
            page.draw_rect(rect, color=rgb, fill=rgb)
            # Texte
            page.insert_text(
                fitz.Point(85, y),
                f"{color_label} — {style.prefix}  {description}",
                fontsize=10,
                color=(0.15, 0.15, 0.15),
            )
            y += 22

        y += 20
        page.insert_text(fitz.Point(50, y), "STATISTIQUES :", fontsize=11,
                         fontname="helv", color=(0.1, 0.1, 0.1))
        y += 20

        stats_lines = [
            f"Total annotations : {self.stats.total}",
            f"  Annotations positionnées sur le texte : {self.stats.placed}",
            f"  Annotations en marge (texte non localisé) : {self.stats.fallback}",
        ]
        for line in stats_lines:
            page.insert_text(fitz.Point(50, y), line, fontsize=10, color=(0.2, 0.2, 0.2))
            y += 16

        y += 10
        page.insert_text(fitz.Point(50, y), "Par catégorie :", fontsize=10, color=(0.3, 0.3, 0.3))
        y += 16
        for cat, count in sorted(self.stats.by_category.items(), key=lambda x: -x[1]):
            style = ANNOTATION_STYLES.get(cat, _DEFAULT_STYLE)
            page.insert_text(fitz.Point(65, y),
                              f"{style.prefix}  {count} annotation(s)",
                              fontsize=9, color=(0.3, 0.3, 0.3))
            y += 14
