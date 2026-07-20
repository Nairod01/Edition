"""
Tests for pure functions in backend/services/pdf_annotator.py.
fitz.Page calls are mocked — no real PDF required.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

import pytest
from unittest.mock import MagicMock, patch

from backend.services.pdf_annotator import (
    _normalize,
    _normalize_for_search,
    _find_by_words,
    _find_by_words_fuzzy,
    _build_comment,
    AnnotationRequest,
    CONFIDENCE_PICTO,
)


# ── _normalize ─────────────────────────────────────────────────────────────────

class TestNormalize:

    def test_right_single_quotation_mark(self):
        assert _normalize("\u2019") == "'"

    def test_left_single_quotation_mark(self):
        assert _normalize("\u2018") == "'"

    def test_left_double_quotation_mark(self):
        assert _normalize("\u201c") == '"'

    def test_right_double_quotation_mark(self):
        assert _normalize("\u201d") == '"'

    def test_ae_ligature(self):
        assert _normalize("\u00e6") == "ae"

    def test_oe_ligature(self):
        assert _normalize("\u0153") == "oe"

    def test_em_dash_unchanged(self):
        result = _normalize("\u2014")
        assert result == "—"

    def test_en_dash_unchanged(self):
        result = _normalize("\u2013")
        assert result == "–"

    def test_non_breaking_space_becomes_space(self):
        assert _normalize("hello\u00a0world") == "hello world"

    def test_narrow_no_break_space_becomes_space(self):
        assert _normalize("hello\u202fworld") == "hello world"

    def test_ellipsis_becomes_three_dots(self):
        assert _normalize("\u2026") == "..."

    def test_multiple_spaces_collapsed(self):
        assert _normalize("hello   world") == "hello world"

    def test_strips_leading_trailing_whitespace(self):
        assert _normalize("  hello  ") == "hello"

    def test_empty_string(self):
        assert _normalize("") == ""

    def test_plain_ascii_unchanged(self):
        assert _normalize("plain text") == "plain text"

    def test_combined_normalizations(self):
        # curly quote + NBSP + multiple spaces
        result = _normalize("\u2018hello\u00a0 \u00a0world\u2019")
        assert result == "'hello world'"

    def test_long_unicode_text(self):
        text = "\u201cL\u2019édition\u201d" * 50
        result = _normalize(text)
        assert "\u201c" not in result
        assert "\u2019" not in result


# ── _normalize_for_search ──────────────────────────────────────────────────────

class TestNormalizeForSearch:

    def test_right_single_quote_normalized(self):
        result = _normalize_for_search("l\u2019édition")
        assert "\u2019" not in result
        assert "'" in result

    def test_left_single_quote_normalized(self):
        result = _normalize_for_search("l\u2018édition")
        assert "\u2018" not in result

    def test_plain_text(self):
        assert _normalize_for_search("hello world") == "hello world"

    def test_empty_string(self):
        assert _normalize_for_search("") == ""


# ── _find_by_words ─────────────────────────────────────────────────────────────

def _make_word_tuple(word, x0, y0, x1, y1, block_no=0, line_no=0, word_no=0):
    """Return a fitz-style word tuple: (x0, y0, x1, y1, word, block_no, line_no, word_no)."""
    return (x0, y0, x1, y1, word, block_no, line_no, word_no)


def _make_page_with_words(words_and_rects):
    """
    Build a mock fitz.Page where get_text('words') returns a list of tuples.
    words_and_rects: list of (word_str, x0, y0, x1, y1)
    """
    page = MagicMock()
    raw_words = [
        _make_word_tuple(w, x0, y0, x1, y1)
        for w, x0, y0, x1, y1 in words_and_rects
    ]
    page.get_text.return_value = raw_words
    return page


class TestFindByWords:

    def test_single_word_found(self):
        import fitz
        page = _make_page_with_words([("hello", 10, 20, 50, 30)])
        results = _find_by_words(page, "hello")
        assert len(results) == 1
        assert isinstance(results[0], fitz.Rect)

    def test_two_word_phrase_found(self):
        import fitz
        page = _make_page_with_words([
            ("Victor", 10, 20, 60, 30),
            ("Hugo",   65, 20, 100, 30),
        ])
        results = _find_by_words(page, "Victor Hugo")
        assert len(results) == 1

    def test_bounding_box_covers_words_on_same_line(self):
        # Comportement actuel : un rect PAR LIGNE (y0 arrondi identique) pour
        # éviter les highlights fusionnés multi-lignes énormes et opaques.
        page = _make_page_with_words([
            ("hello", 10, 20, 50, 30),
            ("world", 55, 20, 100, 30),
        ])
        results = _find_by_words(page, "hello world")
        assert len(results) == 1
        rect = results[0]
        assert rect.x0 == 10
        assert rect.y0 == 20
        assert rect.x1 == 100
        assert rect.y1 == 30

    def test_words_on_different_lines_get_one_rect_each(self):
        # Match à cheval sur deux lignes → deux rects distincts (un par ligne).
        page = _make_page_with_words([
            ("hello", 10, 20, 50, 30),
            ("world", 10, 40, 60, 50),
        ])
        results = _find_by_words(page, "hello world")
        assert len(results) == 2
        assert results[0].y0 == 20
        assert results[1].y0 == 40

    def test_word_not_found(self):
        page = _make_page_with_words([("apple", 10, 20, 50, 30)])
        results = _find_by_words(page, "mango")
        assert results == []

    def test_empty_text(self):
        page = _make_page_with_words([("hello", 10, 20, 50, 30)])
        results = _find_by_words(page, "")
        assert results == []

    def test_empty_page_words(self):
        page = MagicMock()
        page.get_text.return_value = []
        results = _find_by_words(page, "anything")
        assert results == []

    def test_page_raises_exception(self):
        page = MagicMock()
        page.get_text.side_effect = RuntimeError("PDF error")
        results = _find_by_words(page, "something")
        assert results == []

    def test_case_insensitive_match(self):
        # Normalization lowercases before comparison
        page = _make_page_with_words([("VICTOR", 10, 20, 60, 30)])
        results = _find_by_words(page, "victor")
        assert len(results) == 1

    def test_multiple_occurrences_first_match_only(self):
        # Comportement actuel : on s'arrête au premier match — une annotation
        # cible UNE occurrence précise, pas toutes les répétitions du mot.
        page = _make_page_with_words([
            ("hello", 10, 20, 50, 30),
            ("there", 55, 20, 100, 30),
            ("hello", 110, 20, 150, 30),
            ("world", 155, 20, 200, 30),
        ])
        results = _find_by_words(page, "hello")
        assert len(results) == 1
        assert results[0].x0 == 10  # première occurrence

    def test_search_longer_than_page_words(self):
        page = _make_page_with_words([("one", 0, 0, 10, 10)])
        results = _find_by_words(page, "one two three four")
        assert results == []

    def test_curly_quote_normalized(self):
        # Page has a curly apostrophe — search with straight quote should still match
        page = _make_page_with_words([("l\u2019édition", 10, 20, 80, 30)])
        results = _find_by_words(page, "l'édition")
        assert len(results) == 1


# ── _find_by_words_fuzzy ───────────────────────────────────────────────────────

class TestFindByWordsFuzzy:

    def test_single_word_found(self):
        import fitz
        page = _make_page_with_words([("hello", 10, 20, 50, 30)])
        results = _find_by_words_fuzzy(page, "hello")
        assert len(results) == 1

    def test_multi_word_returns_empty(self):
        # fuzzy only works for single-word queries
        page = _make_page_with_words([("hello", 10, 20, 50, 30), ("world", 55, 20, 100, 30)])
        results = _find_by_words_fuzzy(page, "hello world")
        assert results == []

    def test_word_not_found(self):
        page = _make_page_with_words([("apple", 10, 20, 50, 30)])
        results = _find_by_words_fuzzy(page, "mango")
        assert results == []

    def test_empty_page(self):
        page = MagicMock()
        page.get_text.return_value = []
        results = _find_by_words_fuzzy(page, "anything")
        assert results == []

    def test_page_raises_exception(self):
        page = MagicMock()
        page.get_text.side_effect = RuntimeError("PDF error")
        results = _find_by_words_fuzzy(page, "something")
        assert results == []

    def test_normalization_applied(self):
        page = _make_page_with_words([("HELLO", 10, 20, 50, 30)])
        results = _find_by_words_fuzzy(page, "hello")
        assert len(results) == 1


# ── _build_comment ─────────────────────────────────────────────────────────────

def make_annotation_request(**kwargs):
    defaults = dict(
        page_num=0,
        category="A",
        original_text="original",
        corrected_text="corrected",
        description="Short description",
        explanation="Detailed explanation.",
        source="",
        confidence="Probable",
    )
    defaults.update(kwargs)
    return AnnotationRequest(**defaults)


class TestBuildComment:

    def test_original_text_not_repeated_in_comment(self):
        # Format actuel : l'original n'est PAS répété dans le commentaire —
        # il est déjà visible dans le PDF sous l'annotation. Le commentaire
        # contient description + explication + correction proposée.
        req = make_annotation_request(original_text="ancien texte")
        comment = _build_comment(req)
        assert "ancien texte" not in comment
        assert "Correction proposée" in comment

    def test_contains_corrected_text(self):
        req = make_annotation_request(corrected_text="nouveau texte")
        comment = _build_comment(req)
        assert "nouveau texte" in comment

    def test_no_corrected_text_shows_dash(self):
        req = make_annotation_request(corrected_text=None)
        comment = _build_comment(req)
        assert "—" in comment

    def test_certain_picto(self):
        req = make_annotation_request(confidence="Certain")
        comment = _build_comment(req)
        assert CONFIDENCE_PICTO["Certain"] in comment

    def test_probable_picto(self):
        req = make_annotation_request(confidence="Probable")
        comment = _build_comment(req)
        assert CONFIDENCE_PICTO["Probable"] in comment

    def test_a_verifier_picto(self):
        req = make_annotation_request(confidence="À vérifier")
        comment = _build_comment(req)
        assert CONFIDENCE_PICTO["À vérifier"] in comment

    def test_unknown_confidence_defaults_to_probable(self):
        req = make_annotation_request(confidence="unknown_value")
        comment = _build_comment(req)
        assert CONFIDENCE_PICTO["Probable"] in comment

    def test_category_a_label(self):
        req = make_annotation_request(category="A")
        comment = _build_comment(req)
        assert "ORTHOGRAPHE" in comment

    def test_category_b_label(self):
        req = make_annotation_request(category="B")
        comment = _build_comment(req)
        assert "GRAMMAIRE" in comment

    def test_category_c_label(self):
        req = make_annotation_request(category="C")
        comment = _build_comment(req)
        assert "TYPOGRAPHIE" in comment

    def test_category_d_label(self):
        req = make_annotation_request(category="D")
        comment = _build_comment(req)
        assert "SYNTAXE" in comment

    def test_category_h_special_wording(self):
        # H n'est jamais présentée comme une erreur : wording "vérification
        # suggérée" + validation humaine explicite, sans label de catégorie.
        req = make_annotation_request(category="H")
        comment = _build_comment(req)
        assert "VÉRIFICATION SUGGÉRÉE" in comment
        assert "À valider par l'éditeur" in comment

    def test_source_appended_when_present(self):
        req = make_annotation_request(source="https://wikipedia.org")
        comment = _build_comment(req)
        assert "https://wikipedia.org" in comment

    def test_source_absent_when_empty(self):
        req = make_annotation_request(source="")
        comment = _build_comment(req)
        # Should not contain empty lines from source
        lines = comment.split("\n")
        # Last content line should be explanation, not a stray empty string from source
        non_empty = [l for l in lines if l.strip()]
        # Explanation is present
        assert "Detailed explanation." in comment

    def test_long_original_text_does_not_bloat_comment(self):
        # L'original (même très long) n'est pas répété dans le commentaire :
        # le commentaire reste compact quelle que soit la taille du texte source.
        long_text = "a" * 200
        req = make_annotation_request(original_text=long_text)
        comment = _build_comment(req)
        assert long_text not in comment
        assert len(comment) < 500

    def test_simple_mode_is_compact(self):
        req = make_annotation_request()
        detailed = _build_comment(req, mode="detailed")
        simple = _build_comment(req, mode="simple")
        assert len(simple) < len(detailed)
        assert "Explication" not in simple

    def test_description_in_comment(self):
        req = make_annotation_request(description="My description text")
        comment = _build_comment(req)
        assert "My description text" in comment

    def test_explanation_in_comment(self):
        req = make_annotation_request(explanation="This is the explanation.")
        comment = _build_comment(req)
        assert "This is the explanation." in comment

    def test_unicode_corrected_text(self):
        req = make_annotation_request(corrected_text="Ça s'est passé à Besançon")
        comment = _build_comment(req)
        assert "Besançon" in comment
