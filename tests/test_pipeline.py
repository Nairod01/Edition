"""
Tests for pure functions in backend/services/pipeline.py.
No real PDFs, no DB, no API calls.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

import pytest
from unittest.mock import MagicMock, patch

from backend.services.pdf_extractor import ExtractionResult, PageText
from backend.services.fact_checker import FactCheckItem
from backend.services.pipeline import (
    _texts_overlap,
    _deduplicate,
    _build_fact_items,
    _find_page_for_text,
    _build_all_fact_items_from_extraction,
    _build_date_items_from_extraction,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_extraction(pages_text=None, proper_nouns=None, dates=None, titles=None):
    if pages_text is None:
        pages_text = ["Sample text page one.", "Sample text page two."]
    pages = [
        PageText(page_num=i, text=t, word_count=len(t.split()), blocks=[])
        for i, t in enumerate(pages_text)
    ]
    return ExtractionResult(
        pages=pages,
        total_pages=len(pages),
        total_words=sum(p.word_count for p in pages),
        full_text="\n".join(f"[PAGE {i+1}]\n{t}" for i, t in enumerate(pages_text)),
        proper_nouns=proper_nouns or [],
        dates=dates or [],
        defined_terms=[],
        placeholders=[],
        titles=titles or [],
    )


def make_correction(page=0, category="A", original="some text"):
    return {
        "page_number": page,
        "category": category,
        "original_text": original,
        "corrected_text": original,
        "description": "desc",
        "explanation": "expl",
        "source": "",
        "confidence": "Probable",
        "annotation_type": "StrikeOut",
        "color_r": 1.0,
        "color_g": 0.0,
        "color_b": 0.0,
    }


# ── _texts_overlap ─────────────────────────────────────────────────────────────

class TestTextsOverlap:

    def test_shorter_contained_in_longer(self):
        assert _texts_overlap("hello world", "say hello world today") is True

    def test_longer_contains_shorter(self):
        assert _texts_overlap("say hello world today", "hello world") is True

    def test_no_overlap(self):
        assert _texts_overlap("apple", "orange juice") is False

    def test_empty_a(self):
        assert _texts_overlap("", "some text") is False

    def test_empty_b(self):
        assert _texts_overlap("some text", "") is False

    def test_both_empty(self):
        assert _texts_overlap("", "") is False

    def test_shorter_than_five_chars(self):
        # "abc" is 3 chars — below the 5-char threshold
        assert _texts_overlap("abc", "abcdefgh") is False

    def test_exact_five_chars_overlap(self):
        # "hello" is exactly 5 chars — threshold is < 5, so 5 is valid
        assert _texts_overlap("hello", "say hello there") is True

    def test_identical_strings(self):
        assert _texts_overlap("identical text", "identical text") is True

    def test_unicode_strings(self):
        assert _texts_overlap("Éditeur français", "Le grand Éditeur français moderne") is True

    def test_case_sensitive(self):
        # _texts_overlap is case-sensitive
        assert _texts_overlap("Hello World", "hello world") is False


# ── _deduplicate ───────────────────────────────────────────────────────────────

class TestDeduplicate:

    def test_empty_list(self):
        assert _deduplicate([]) == []

    def test_no_duplicates(self):
        corrections = [
            make_correction(page=0, category="A", original="first text"),
            make_correction(page=1, category="B", original="second text"),
        ]
        result = _deduplicate(corrections)
        assert len(result) == 2

    def test_exact_duplicate_same_page_keeps_higher_priority(self):
        c1 = make_correction(page=0, category="C", original="duplicated text")
        c2 = make_correction(page=0, category="A", original="duplicated text")
        result = _deduplicate([c1, c2])
        assert len(result) == 1
        assert result[0]["category"] == "A"

    def test_exact_duplicate_case_insensitive(self):
        c1 = make_correction(page=0, category="B", original="Duplicated Text")
        c2 = make_correction(page=0, category="A", original="duplicated text")
        result = _deduplicate([c1, c2])
        assert len(result) == 1

    def test_different_pages_no_dedup(self):
        c1 = make_correction(page=0, category="A", original="same text here")
        c2 = make_correction(page=1, category="A", original="same text here")
        result = _deduplicate([c1, c2])
        assert len(result) == 2

    def test_overlap_dedup_same_page(self):
        # A (priority 1) is shorter and contained in B's text on same page
        c_a = make_correction(page=0, category="A", original="short phrase")
        c_b = make_correction(page=0, category="B", original="this is a short phrase longer")
        result = _deduplicate([c_b, c_a])  # B first, then A (higher prio)
        # A has higher priority — B should be removed as it overlaps with A's shorter text
        assert len(result) == 1

    def test_overlap_dedup_different_pages_kept(self):
        c1 = make_correction(page=0, category="A", original="short phrase")
        c2 = make_correction(page=1, category="B", original="this short phrase is here")
        result = _deduplicate([c1, c2])
        assert len(result) == 2

    def test_single_item(self):
        c = make_correction(page=0, category="A", original="lonely correction")
        result = _deduplicate([c])
        assert len(result) == 1

    def test_h_category_priority(self):
        # H has lowest priority (8)
        c_h = make_correction(page=0, category="H", original="Victor Hugo")
        c_a = make_correction(page=0, category="A", original="Victor Hugo")
        result = _deduplicate([c_h, c_a])
        assert len(result) == 1
        assert result[0]["category"] == "A"

    def test_preserves_content_fields(self):
        c = make_correction(page=2, category="C", original="some unique text here indeed")
        c["description"] = "my description"
        c["explanation"] = "my explanation"
        result = _deduplicate([c])
        assert result[0]["description"] == "my description"
        assert result[0]["explanation"] == "my explanation"


# ── _build_fact_items ──────────────────────────────────────────────────────────

class TestBuildFactItems:

    def test_empty_input(self):
        result = _build_fact_items([])
        assert result == []

    def test_basic_proper_noun(self):
        fd = {"text": "Victor Hugo", "item_type": "proper_noun", "page_hint": 1, "context": "author"}
        result = _build_fact_items([fd])
        assert len(result) == 1
        assert result[0].query == "Victor Hugo"
        assert result[0].item_type == "proper_noun"
        assert result[0].page_num == 0  # page_hint=1 → page_num=0

    def test_basic_date(self):
        fd = {"text": "14 juillet 1789", "item_type": "date", "page_hint": 3, "context": "ctx"}
        result = _build_fact_items([fd])
        assert len(result) == 1
        assert result[0].item_type == "date"
        assert result[0].page_num == 2

    def test_basic_title(self):
        fd = {"text": "Les Misérables", "item_type": "title", "page_hint": 2}
        result = _build_fact_items([fd])
        assert len(result) == 1
        assert result[0].item_type == "title"

    def test_unknown_item_type_defaults_to_proper_noun(self):
        fd = {"text": "something weird", "item_type": "unknown_type", "page_hint": 1}
        result = _build_fact_items([fd])
        assert result[0].item_type == "proper_noun"

    def test_deduplicates_same_text(self):
        fd1 = {"text": "Victor Hugo", "item_type": "proper_noun", "page_hint": 1}
        fd2 = {"text": "Victor Hugo", "item_type": "proper_noun", "page_hint": 2}
        result = _build_fact_items([fd1, fd2])
        assert len(result) == 1

    def test_skips_empty_text(self):
        fd = {"text": "", "item_type": "proper_noun", "page_hint": 1}
        result = _build_fact_items([fd])
        assert len(result) == 0

    def test_skips_none_text(self):
        fd = {"text": None, "item_type": "proper_noun", "page_hint": 1}
        result = _build_fact_items([fd])
        assert len(result) == 0

    def test_max_items_respected(self):
        fds = [{"text": f"Item {i}", "item_type": "proper_noun", "page_hint": 1} for i in range(50)]
        result = _build_fact_items(fds, max_items=10)
        assert len(result) <= 10

    def test_context_truncated_at_300(self):
        long_context = "x" * 500
        fd = {"text": "Victor Hugo", "item_type": "proper_noun", "page_hint": 1, "context": long_context}
        result = _build_fact_items([fd])
        assert len(result[0].context) <= 300

    def test_context_falls_back_to_text_when_missing(self):
        fd = {"text": "Victor Hugo", "item_type": "proper_noun", "page_hint": 1}
        result = _build_fact_items([fd])
        assert result[0].context == "Victor Hugo"

    def test_page_hint_zero(self):
        fd = {"text": "Some Name", "item_type": "proper_noun", "page_hint": 0}
        result = _build_fact_items([fd])
        # page_hint=0 → max(0, 0-1) = 0
        assert result[0].page_num == 0


# ── _find_page_for_text ────────────────────────────────────────────────────────

class TestFindPageForText:

    def test_text_found_on_first_page(self):
        extraction = make_extraction(["Victor Hugo naquit en 1802.", "Autre texte ici."])
        assert _find_page_for_text(extraction, "Victor Hugo") == 0

    def test_text_found_on_second_page(self):
        extraction = make_extraction(["Page one content.", "Victor Hugo naquit en 1802."])
        assert _find_page_for_text(extraction, "Victor Hugo") == 1

    def test_text_not_found_returns_zero(self):
        extraction = make_extraction(["Page one content.", "Page two content."])
        assert _find_page_for_text(extraction, "Marie Curie") == 0

    def test_empty_extraction(self):
        extraction = make_extraction([])
        assert _find_page_for_text(extraction, "anything") == 0

    def test_text_on_last_page(self):
        extraction = make_extraction(["a", "b", "c", "Victor Hugo naquit."])
        assert _find_page_for_text(extraction, "Victor Hugo") == 3

    def test_unicode_text_found(self):
        extraction = make_extraction(["Ça s'est passé à Besançon.", "Autre."])
        assert _find_page_for_text(extraction, "Besançon") == 0


# ── _build_all_fact_items_from_extraction ──────────────────────────────────────

class TestBuildAllFactItemsFromExtraction:

    def test_empty_extraction(self):
        extraction = make_extraction([])
        result = _build_all_fact_items_from_extraction(extraction)
        assert isinstance(result, list)
        assert len(result) == 0

    def test_dates_included(self):
        dates = [
            {"text": "14 juillet 1789", "context": "La Révolution française", "page": 0},
        ]
        extraction = make_extraction(dates=dates)
        result = _build_all_fact_items_from_extraction(extraction)
        texts = [r.original_text for r in result]
        assert "14 juillet 1789" in texts

    def test_proper_nouns_included(self):
        extraction = make_extraction(
            pages_text=["Victor Hugo naquit ici."],
            proper_nouns=["Victor Hugo"],
        )
        result = _build_all_fact_items_from_extraction(extraction)
        texts = [r.original_text for r in result]
        assert "Victor Hugo" in texts

    def test_titles_included(self):
        extraction = make_extraction(
            titles=[{"text": "Les Misérables", "page": 0}]
        )
        result = _build_all_fact_items_from_extraction(extraction)
        texts = [r.original_text for r in result]
        assert "Les Misérables" in texts

    def test_no_duplicates_across_types(self):
        # Same text in dates and proper_nouns should appear only once
        extraction = make_extraction(
            pages_text=["1789 texte."],
            proper_nouns=["1789"],
            dates=[{"text": "1789", "context": "ctx", "page": 0}],
        )
        result = _build_all_fact_items_from_extraction(extraction)
        texts = [r.original_text for r in result]
        assert texts.count("1789") == 1

    def test_max_items_respected(self):
        dates = [{"text": f"date {i}", "context": "ctx", "page": 0} for i in range(30)]
        proper_nouns = [f"Name {i}" for i in range(20)]
        extraction = make_extraction(dates=dates, proper_nouns=proper_nouns)
        result = _build_all_fact_items_from_extraction(extraction, max_items=10)
        assert len(result) <= 10

    def test_proper_noun_page_resolved(self):
        extraction = make_extraction(
            pages_text=["First page.", "Victor Hugo was here."],
            proper_nouns=["Victor Hugo"],
        )
        result = _build_all_fact_items_from_extraction(extraction)
        victor_items = [r for r in result if r.original_text == "Victor Hugo"]
        assert len(victor_items) == 1
        assert victor_items[0].page_num == 1

    def test_date_item_type_is_date(self):
        dates = [{"text": "14 juillet 1789", "context": "ctx", "page": 0}]
        extraction = make_extraction(dates=dates)
        result = _build_all_fact_items_from_extraction(extraction)
        date_items = [r for r in result if r.original_text == "14 juillet 1789"]
        assert date_items[0].item_type == "date"

    def test_title_item_type_is_title(self):
        extraction = make_extraction(titles=[{"text": "Les Misérables", "page": 0}])
        result = _build_all_fact_items_from_extraction(extraction)
        title_items = [r for r in result if r.original_text == "Les Misérables"]
        assert title_items[0].item_type == "title"


# ── _build_date_items_from_extraction ─────────────────────────────────────────

class TestBuildDateItemsFromExtraction:

    def test_empty_dates(self):
        extraction = make_extraction(dates=[])
        result = _build_date_items_from_extraction(extraction)
        assert result == []

    def test_single_date(self):
        dates = [{"text": "26 février 1802", "context": "Victor Hugo naquit le 26 février 1802.", "page": 0}]
        extraction = make_extraction(dates=dates)
        result = _build_date_items_from_extraction(extraction)
        assert len(result) == 1
        assert result[0].original_text == "26 février 1802"
        assert result[0].item_type == "date"

    def test_deduplicates_same_date_text(self):
        dates = [
            {"text": "1789", "context": "ctx1", "page": 0},
            {"text": "1789", "context": "ctx2", "page": 1},
        ]
        extraction = make_extraction(dates=dates)
        result = _build_date_items_from_extraction(extraction)
        assert len(result) == 1

    def test_max_dates_respected(self):
        dates = [{"text": f"année {i+1000}", "context": "ctx", "page": 0} for i in range(30)]
        extraction = make_extraction(dates=dates)
        result = _build_date_items_from_extraction(extraction, max_dates=5)
        assert len(result) <= 5

    def test_page_num_correct(self):
        dates = [{"text": "1902", "context": "ctx", "page": 3}]
        extraction = make_extraction(dates=dates)
        result = _build_date_items_from_extraction(extraction)
        assert result[0].page_num == 3

    def test_skips_empty_text(self):
        dates = [{"text": "", "context": "ctx", "page": 0}]
        extraction = make_extraction(dates=dates)
        result = _build_date_items_from_extraction(extraction)
        assert len(result) == 0

    def test_skips_none_text(self):
        dates = [{"text": None, "context": "ctx", "page": 0}]
        extraction = make_extraction(dates=dates)
        result = _build_date_items_from_extraction(extraction)
        assert len(result) == 0

    def test_context_truncated(self):
        long_context = "a" * 500
        dates = [{"text": "1802", "context": long_context, "page": 0}]
        extraction = make_extraction(dates=dates)
        result = _build_date_items_from_extraction(extraction)
        assert len(result[0].context) <= 300

    def test_multiple_dates_distinct(self):
        dates = [
            {"text": "1789", "context": "ctx", "page": 0},
            {"text": "1802", "context": "ctx", "page": 1},
            {"text": "1945", "context": "ctx", "page": 2},
        ]
        extraction = make_extraction(dates=dates)
        result = _build_date_items_from_extraction(extraction)
        assert len(result) == 3
