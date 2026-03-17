"""
Extraction du texte et de la structure d'un PDF manuscrit ou maquetté.
Utilise PyMuPDF pour une extraction fidèle aux positions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF


# ─────────────────────────────────────────────
# Structures de données
# ─────────────────────────────────────────────

@dataclass
class TextSpan:
    """Unité minimale de texte avec ses propriétés typographiques."""
    text: str
    font_name: str
    font_size: float
    is_bold: bool
    is_italic: bool
    color: int  # couleur encodée en int (0 = noir)
    rect: tuple[float, float, float, float]  # x0, y0, x1, y1


@dataclass
class TextLine:
    """Ligne de texte composée de spans."""
    spans: list[TextSpan]
    rect: tuple[float, float, float, float]

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass
class TextBlock:
    """Bloc de texte (paragraphe, titre, note, etc.) avec métadonnées."""
    page_num: int           # 0-indexé
    block_index: int
    lines: list[TextLine]
    rect: tuple[float, float, float, float]
    block_type: str         # paragraph | title | subtitle | footnote | header | footer | caption | other

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def full_text(self) -> str:
        """Texte complet sans sauts de ligne internes."""
        return " ".join(line.text.strip() for line in self.lines if line.text.strip())

    @property
    def font_size(self) -> float:
        """Taille de police dominante dans le bloc."""
        sizes = [s.font_size for line in self.lines for s in line.spans if s.text.strip()]
        return max(set(sizes), key=sizes.count) if sizes else 0.0

    @property
    def is_bold(self) -> bool:
        bold_chars = sum(len(s.text) for line in self.lines for s in line.spans if s.is_bold)
        total_chars = sum(len(s.text) for line in self.lines for s in line.spans)
        return bold_chars > total_chars * 0.5 if total_chars else False

    @property
    def is_italic(self) -> bool:
        italic_chars = sum(len(s.text) for line in self.lines for s in line.spans if s.is_italic)
        total_chars = sum(len(s.text) for line in self.lines for s in line.spans)
        return italic_chars > total_chars * 0.5 if total_chars else False


@dataclass
class PageContent:
    """Contenu complet d'une page."""
    page_num: int
    width: float
    height: float
    blocks: list[TextBlock]
    raw_text: str           # texte brut pour recherches rapides
    has_images: bool
    is_scanned: bool        # true si la page est une image sans texte extractible


@dataclass
class DocumentStructure:
    """Structure globale du document."""
    pages: list[PageContent]
    title: str
    dominant_font_size: float       # taille de corps de texte dominant
    title_font_sizes: list[float]   # tailles utilisées pour les titres
    page_count: int
    has_footnotes: bool
    has_headers_footers: bool
    is_multicolumn: bool
    entities: dict = field(default_factory=dict)   # noms propres détectés
    chapter_pages: list[int] = field(default_factory=list)  # pages de début de chapitre


# ─────────────────────────────────────────────
# Extraction principale
# ─────────────────────────────────────────────

class PDFExtractor:
    """Extrait le contenu structuré d'un PDF."""

    # Seuil en dessous duquel on considère que la page n'a pas assez de texte (scannée)
    MIN_TEXT_CHARS_PER_PAGE = 50

    def __init__(self, pdf_path: str | Path):
        self.pdf_path = Path(pdf_path)
        self._doc: fitz.Document = fitz.open(str(self.pdf_path))

    def extract(self) -> DocumentStructure:
        """Point d'entrée principal : retourne la structure complète du document."""
        pages = [self._extract_page(i) for i in range(len(self._doc))]
        structure = self._analyze_structure(pages)
        return structure

    def close(self):
        self._doc.close()

    # ── Extraction par page ──────────────────

    def _extract_page(self, page_num: int) -> PageContent:
        page: fitz.Page = self._doc[page_num]
        raw_text = page.get_text("text")
        is_scanned = len(raw_text.strip()) < self.MIN_TEXT_CHARS_PER_PAGE

        blocks = []
        if not is_scanned:
            raw_blocks = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
            text_blocks = [b for b in raw_blocks if b["type"] == 0]  # type 0 = texte
            for idx, b in enumerate(text_blocks):
                block = self._parse_block(b, page_num, idx, page)
                if block and block.full_text.strip():
                    blocks.append(block)

        has_images = any(b["type"] == 1 for b in page.get_text("rawdict")["blocks"]) if not is_scanned else True

        return PageContent(
            page_num=page_num,
            width=page.rect.width,
            height=page.rect.height,
            blocks=blocks,
            raw_text=raw_text,
            has_images=has_images,
            is_scanned=is_scanned,
        )

    def _parse_block(self, raw_block: dict, page_num: int, block_index: int, page: fitz.Page) -> Optional[TextBlock]:
        lines = []
        for raw_line in raw_block.get("lines", []):
            spans = []
            for raw_span in raw_line.get("spans", []):
                text = raw_span.get("text", "")
                if not text:
                    continue
                flags = raw_span.get("flags", 0)
                span = TextSpan(
                    text=text,
                    font_name=raw_span.get("font", ""),
                    font_size=round(raw_span.get("size", 12), 1),
                    is_bold=bool(flags & 2**4),   # bit 4 = bold
                    is_italic=bool(flags & 2**1),  # bit 1 = italic
                    color=raw_span.get("color", 0),
                    rect=tuple(raw_span.get("bbox", (0, 0, 0, 0))),
                )
                spans.append(span)
            if spans:
                lines.append(TextLine(spans=spans, rect=tuple(raw_line.get("bbox", (0, 0, 0, 0)))))

        if not lines:
            return None

        rect = tuple(raw_block.get("bbox", (0, 0, 0, 0)))
        block_type = self._classify_block(lines, rect, page)

        return TextBlock(
            page_num=page_num,
            block_index=block_index,
            lines=lines,
            rect=rect,
            block_type=block_type,
        )

    # ── Classification des blocs ─────────────

    def _classify_block(self, lines: list[TextLine], rect: tuple, page: fitz.Page) -> str:
        """Classe un bloc en fonction de sa position et de sa typographie."""
        if not lines:
            return "other"

        page_height = page.rect.height
        page_width = page.rect.width
        y0, y1 = rect[1], rect[3]
        x0, x1 = rect[0], rect[2]

        dominant_size = self._dominant_font_size(lines)
        text = " ".join(line.text for line in lines).strip()

        # En-têtes et pieds de page (zones haute et basse)
        if y1 < page_height * 0.08:
            return "header"
        if y0 > page_height * 0.92:
            return "footer"

        # Notes de bas de page : petite taille, zone basse
        if y0 > page_height * 0.75 and dominant_size < 10:
            return "footnote"

        # Numéro de page seul
        if re.match(r"^\s*\d+\s*$", text):
            return "page_number"

        # Titres : grande taille ou centré avec peu de texte
        is_centered = abs((x0 + x1) / 2 - page_width / 2) < page_width * 0.15
        is_short = len(text) < 80
        if dominant_size >= 14 or (is_centered and is_short and len(lines) <= 3):
            all_bold = all(s.is_bold for line in lines for s in line.spans if s.text.strip())
            if dominant_size >= 18 or all_bold:
                return "title"
            return "subtitle"

        return "paragraph"

    def _dominant_font_size(self, lines: list[TextLine]) -> float:
        sizes = [s.font_size for line in lines for s in line.spans if s.text.strip()]
        if not sizes:
            return 0.0
        return max(set(sizes), key=sizes.count)

    # ── Analyse de la structure globale ─────

    def _analyze_structure(self, pages: list[PageContent]) -> DocumentStructure:
        """Détecte la structure éditoriale globale du document."""
        all_blocks = [b for p in pages for b in p.blocks]

        # Taille de corps dominante (on exclut les blocs de type titre/footnote)
        body_sizes = [
            b.font_size for b in all_blocks
            if b.block_type in ("paragraph",) and b.font_size > 0
        ]
        dominant_size = max(set(body_sizes), key=body_sizes.count) if body_sizes else 12.0

        # Tailles utilisées pour les titres
        title_sizes = sorted(set(
            b.font_size for b in all_blocks
            if b.block_type in ("title", "subtitle") and b.font_size > dominant_size
        ), reverse=True)

        # Pages de début de chapitre
        chapter_pages = [
            b.page_num for b in all_blocks
            if b.block_type == "title" and b.font_size >= dominant_size * 1.3
        ]

        has_footnotes = any(b.block_type == "footnote" for b in all_blocks)
        has_headers_footers = any(b.block_type in ("header", "footer") for b in all_blocks)
        is_multicolumn = self._detect_multicolumn(pages)
        title = self._extract_title(pages)

        return DocumentStructure(
            pages=pages,
            title=title,
            dominant_font_size=dominant_size,
            title_font_sizes=title_sizes,
            page_count=len(pages),
            has_footnotes=has_footnotes,
            has_headers_footers=has_headers_footers,
            is_multicolumn=is_multicolumn,
            chapter_pages=chapter_pages,
        )

    def _detect_multicolumn(self, pages: list[PageContent]) -> bool:
        """Détecte si le document utilise une mise en page multi-colonnes."""
        for page in pages[:10]:
            if len(page.blocks) < 4:
                continue
            x_centers = [(b.rect[0] + b.rect[2]) / 2 for b in page.blocks if b.block_type == "paragraph"]
            if len(x_centers) < 4:
                continue
            # Si les centres des blocs sont concentrés sur 2 zones x distinctes → multi-colonnes
            left = [x for x in x_centers if x < page.width * 0.5]
            right = [x for x in x_centers if x >= page.width * 0.5]
            if len(left) >= 2 and len(right) >= 2:
                return True
        return False

    def _extract_title(self, pages: list[PageContent]) -> str:
        """Extrait le titre du document depuis les premières pages."""
        for page in pages[:5]:
            for block in page.blocks:
                if block.block_type == "title" and block.full_text.strip():
                    return block.full_text.strip()
        return "Document sans titre"

    # ── Chunking pour l'analyse LLM ─────────

    def get_chunks(self, structure: DocumentStructure, max_tokens: int = 2000, overlap_tokens: int = 200) -> list[dict]:
        """
        Découpe le document en chunks pour l'envoi au LLM.
        Chaque chunk contient le texte + les métadonnées de position.
        """
        chunks = []
        current_chunk_blocks: list[TextBlock] = []
        current_token_count = 0

        all_blocks = [
            b for p in structure.pages
            for b in p.blocks
            if b.block_type in ("paragraph", "title", "subtitle", "footnote")
            and b.full_text.strip()
        ]

        def estimate_tokens(text: str) -> int:
            return len(text) // 4  # estimation grossière

        for block in all_blocks:
            block_tokens = estimate_tokens(block.full_text)

            if current_token_count + block_tokens > max_tokens and current_chunk_blocks:
                chunk = self._make_chunk(current_chunk_blocks)
                chunks.append(chunk)
                # Chevauchement : on garde les derniers blocs
                overlap_blocks = []
                overlap_count = 0
                for b in reversed(current_chunk_blocks):
                    t = estimate_tokens(b.full_text)
                    if overlap_count + t > overlap_tokens:
                        break
                    overlap_blocks.insert(0, b)
                    overlap_count += t
                current_chunk_blocks = overlap_blocks
                current_token_count = overlap_count

            current_chunk_blocks.append(block)
            current_token_count += block_tokens

        if current_chunk_blocks:
            chunks.append(self._make_chunk(current_chunk_blocks))

        return chunks

    def _make_chunk(self, blocks: list[TextBlock]) -> dict:
        return {
            "text": "\n\n".join(b.full_text for b in blocks),
            "start_page": blocks[0].page_num + 1,
            "end_page": blocks[-1].page_num + 1,
            "blocks": blocks,
        }

    def get_full_text_for_global_analysis(self, structure: DocumentStructure) -> str:
        """
        Retourne le texte complet pour une analyse de cohérence globale.
        Résumé par chapitre pour les très longs documents.
        """
        parts = []
        for page in structure.pages:
            page_texts = [b.full_text for b in page.blocks if b.full_text.strip()]
            if page_texts:
                parts.append(f"[Page {page.page_num + 1}]\n" + "\n".join(page_texts))
        return "\n\n".join(parts)
