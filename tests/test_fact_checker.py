"""
Tests for pure functions in backend/services/fact_checker.py.
No API calls — all pure Python logic.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

import pytest

from backend.services.fact_checker import (
    FactCheckItem,
    FactCheckCorrection,
    _is_valid,
    _format_items_for_prompt,
    _parse_anomalies,
    MIN_ITEM_LENGTH,
)


# ── _is_valid ──────────────────────────────────────────────────────────────────

class TestIsValid:

    def test_valid_proper_noun(self):
        assert _is_valid("Victor Hugo") is True

    def test_valid_date_with_letters(self):
        assert _is_valid("14 juillet 1789") is True

    def test_too_short_single_char(self):
        assert _is_valid("a") is False

    def test_too_short_three_chars(self):
        # MIN_ITEM_LENGTH is 4 — "abc" (3 chars) is invalid
        assert _is_valid("abc") is False

    def test_exactly_min_length(self):
        # "abcd" is 4 chars — valid if it has letters
        assert _is_valid("abcd") is True

    def test_only_digits_invalid(self):
        assert _is_valid("1234") is False

    def test_only_digits_short(self):
        assert _is_valid("42") is False

    def test_no_letter_content(self):
        # Only special chars / punctuation
        assert _is_valid("---!") is False

    def test_empty_string(self):
        assert _is_valid("") is False

    def test_whitespace_only(self):
        assert _is_valid("   ") is False

    def test_mixed_digits_and_letters(self):
        # "1789a" has a letter → valid
        assert _is_valid("1789a") is True

    def test_unicode_valid(self):
        assert _is_valid("Ée Çç") is True

    def test_accented_name(self):
        assert _is_valid("Émile Zola") is True

    def test_long_valid_string(self):
        text = "Victor Marie Hugo"
        assert _is_valid(text) is True

    def test_whitespace_stripped_before_check(self):
        # Leading/trailing spaces should not cause a valid item to fail
        assert _is_valid("  Victor  ") is True

    def test_digits_with_slash(self):
        # "12/07" — no letter — invalid
        assert _is_valid("12/07") is False


# ── _format_items_for_prompt ───────────────────────────────────────────────────

def make_item(original="Victor Hugo", item_type="proper_noun", page_num=0, context=None):
    return FactCheckItem(
        query=original,
        context=context or original,
        page_num=page_num,
        original_text=original,
        item_type=item_type,
    )


class TestFormatItemsForPrompt:

    def test_returns_string(self):
        items = [make_item()]
        result = _format_items_for_prompt(items)
        assert isinstance(result, str)

    def test_contains_header(self):
        items = [make_item()]
        result = _format_items_for_prompt(items)
        assert "Éléments à vérifier" in result

    def test_contains_original_text(self):
        items = [make_item(original="Victor Hugo")]
        result = _format_items_for_prompt(items)
        assert "Victor Hugo" in result

    def test_proper_noun_label(self):
        items = [make_item(item_type="proper_noun")]
        result = _format_items_for_prompt(items)
        assert "Nom propre" in result

    def test_date_label(self):
        items = [make_item(original="14 juillet 1789", item_type="date")]
        result = _format_items_for_prompt(items)
        assert "Date historique" in result

    def test_title_label(self):
        items = [make_item(original="Les Misérables", item_type="title")]
        result = _format_items_for_prompt(items)
        assert "Titre d'œuvre" in result

    def test_unknown_type_shows_element_label(self):
        items = [make_item(item_type="custom_type")]
        result = _format_items_for_prompt(items)
        assert "Élément" in result

    def test_page_number_one_indexed(self):
        # page_num=0 → displayed as "page 1"
        items = [make_item(page_num=0)]
        result = _format_items_for_prompt(items)
        assert "page 1" in result

    def test_page_number_three(self):
        items = [make_item(page_num=2)]
        result = _format_items_for_prompt(items)
        assert "page 3" in result

    def test_context_shown_when_different(self):
        items = [make_item(original="Hugo", context="Victor Hugo était un écrivain.")]
        result = _format_items_for_prompt(items)
        assert "Victor Hugo était un écrivain." in result

    def test_context_not_shown_when_same_as_original(self):
        items = [make_item(original="Victor Hugo", context="Victor Hugo")]
        result = _format_items_for_prompt(items)
        # Context line should not appear since it equals original
        lines = result.split("\n")
        context_lines = [l for l in lines if l.startswith("   Contexte")]
        assert len(context_lines) == 0

    def test_multiple_items_numbered(self):
        items = [make_item("Item A"), make_item("Item B"), make_item("Item C")]
        result = _format_items_for_prompt(items)
        assert "1." in result
        assert "2." in result
        assert "3." in result

    def test_empty_items_list(self):
        result = _format_items_for_prompt([])
        assert "Éléments à vérifier" in result

    def test_context_truncated_in_display(self):
        long_context = "x" * 300
        items = [make_item(original="Hugo", context=long_context)]
        result = _format_items_for_prompt(items)
        # The prompt format truncates context at 200 chars
        # Ensure the full 300-char context is NOT in result
        assert "x" * 250 not in result


# ── _parse_anomalies ───────────────────────────────────────────────────────────

def make_raw_anomaly(**kwargs):
    # corrected_text DOIT différer de original_text : _parse_anomalies filtre
    # les anomalies où original == correction (règle anti-faux-positifs).
    defaults = {
        "original_text": "Victor Hugo",
        "corrected_text": "Victor Hugo (graphie corrigée)",
        "page_hint": 1,
        "explanation": "Name is correctly spelled.",
        "source": "https://wikipedia.org",
        "confidence": "Certain",
    }
    defaults.update(kwargs)
    return defaults


def make_items_list():
    return [
        FactCheckItem(
            query="Victor Hugo",
            context="Victor Hugo était un écrivain.",
            page_num=0,
            original_text="Victor Hugo",
            item_type="proper_noun",
        ),
        FactCheckItem(
            query="14 juillet 1789",
            context="La prise de la Bastille le 14 juillet 1789.",
            page_num=2,
            original_text="14 juillet 1789",
            item_type="date",
        ),
    ]


class TestParseAnomalies:

    def test_empty_raw_returns_empty(self):
        result = _parse_anomalies([], {}, [])
        assert result == []

    # RÈGLE ABSOLUE (comportement actuel) : toute correction H sort avec la
    # confiance "À vérifier", quelle que soit la confiance annoncée par Claude.
    # C'est l'éditeur humain qui valide en dernier ressort — aucune anomalie
    # n'est filtrée sur la base de sa confiance.

    def test_certain_confidence_forced_to_a_verifier(self):
        raw = [make_raw_anomaly(confidence="Certain")]
        items = make_items_list()
        page_hints = {"Victor Hugo": 0}
        result = _parse_anomalies(raw, page_hints, items)
        assert len(result) == 1
        assert result[0].confidence == "À vérifier"

    def test_probable_confidence_forced_to_a_verifier(self):
        raw = [make_raw_anomaly(confidence="Probable")]
        items = make_items_list()
        page_hints = {"Victor Hugo": 0}
        result = _parse_anomalies(raw, page_hints, items)
        assert len(result) == 1
        assert result[0].confidence == "À vérifier"

    def test_a_verifier_confidence_kept(self):
        raw = [make_raw_anomaly(confidence="À vérifier")]
        items = make_items_list()
        page_hints = {"Victor Hugo": 0}
        result = _parse_anomalies(raw, page_hints, items)
        assert len(result) == 1
        assert result[0].confidence == "À vérifier"

    def test_unknown_confidence_still_forced(self):
        raw = [make_raw_anomaly(confidence="Maybe")]
        items = make_items_list()
        page_hints = {"Victor Hugo": 0}
        result = _parse_anomalies(raw, page_hints, items)
        assert len(result) == 1
        assert result[0].confidence == "À vérifier"

    def test_original_equals_correction_filtered(self):
        # Règle anti-faux-positifs : si Claude "corrige" vers un texte identique,
        # l'anomalie est un faux positif et doit être ignorée.
        raw = [make_raw_anomaly(corrected_text="Victor Hugo", confidence="Certain")]
        items = make_items_list()
        result = _parse_anomalies(raw, {"Victor Hugo": 0}, items)
        assert result == []

    def test_empty_original_text_skipped(self):
        raw = [make_raw_anomaly(original_text="", confidence="Certain")]
        result = _parse_anomalies(raw, {}, [])
        assert len(result) == 0

    def test_none_original_text_skipped(self):
        raw = [make_raw_anomaly(original_text=None, confidence="Certain")]
        result = _parse_anomalies(raw, {}, [])
        assert len(result) == 0

    def test_page_hint_from_raw(self):
        raw = [make_raw_anomaly(page_hint=5, confidence="Certain")]
        items = make_items_list()
        page_hints = {"Victor Hugo": 0}
        result = _parse_anomalies(raw, page_hints, items)
        assert result[0].page_num == 5

    def test_page_hint_falls_back_to_page_hints_dict(self):
        raw = [make_raw_anomaly(page_hint=None, confidence="Certain")]
        items = make_items_list()
        page_hints = {"Victor Hugo": 3}
        result = _parse_anomalies(raw, page_hints, items)
        assert result[0].page_num == 3

    def test_category_always_h(self):
        raw = [make_raw_anomaly(confidence="Certain")]
        items = make_items_list()
        result = _parse_anomalies(raw, {"Victor Hugo": 0}, items)
        assert result[0].category == "H"

    def test_item_type_proper_noun_description(self):
        raw = [make_raw_anomaly(original_text="Victor Hugo", confidence="Certain")]
        items = make_items_list()
        result = _parse_anomalies(raw, {"Victor Hugo": 0}, items)
        assert "nom propre" in result[0].description.lower()

    def test_item_type_date_description(self):
        raw = [make_raw_anomaly(original_text="14 juillet 1789", confidence="Certain")]
        items = make_items_list()
        result = _parse_anomalies(raw, {"14 juillet 1789": 2}, items)
        assert "date" in result[0].description.lower()

    def test_unknown_original_defaults_to_proper_noun_description(self):
        raw = [make_raw_anomaly(original_text="Unknown Name", confidence="Certain")]
        result = _parse_anomalies(raw, {"Unknown Name": 0}, [])
        assert "nom propre" in result[0].description.lower()

    def test_corrected_text_preserved(self):
        raw = [make_raw_anomaly(corrected_text="Victor Hugo (corrected)", confidence="Certain")]
        items = make_items_list()
        result = _parse_anomalies(raw, {"Victor Hugo": 0}, items)
        assert result[0].corrected_text == "Victor Hugo (corrected)"

    def test_missing_corrected_text_is_none(self):
        raw = [{"original_text": "Victor Hugo", "page_hint": 0, "explanation": "x", "source": "y", "confidence": "Certain"}]
        items = make_items_list()
        result = _parse_anomalies(raw, {"Victor Hugo": 0}, items)
        assert result[0].corrected_text is None

    def test_explanation_truncated_at_1000(self):
        long_explanation = "e" * 1500
        raw = [make_raw_anomaly(explanation=long_explanation, confidence="Certain")]
        items = make_items_list()
        result = _parse_anomalies(raw, {"Victor Hugo": 0}, items)
        assert len(result[0].explanation) <= 1000

    def test_source_truncated_at_300(self):
        long_source = "s" * 500
        raw = [make_raw_anomaly(source=long_source, confidence="Certain")]
        items = make_items_list()
        result = _parse_anomalies(raw, {"Victor Hugo": 0}, items)
        assert len(result[0].source) <= 300

    def test_multiple_anomalies(self):
        raw = [
            make_raw_anomaly(original_text="Victor Hugo", confidence="Certain"),
            make_raw_anomaly(original_text="14 juillet 1789", confidence="Probable"),
        ]
        items = make_items_list()
        page_hints = {"Victor Hugo": 0, "14 juillet 1789": 2}
        result = _parse_anomalies(raw, page_hints, items)
        assert len(result) == 2

    def test_mixed_confidences_all_kept_and_forced(self):
        raw = [
            make_raw_anomaly(original_text="Victor Hugo", confidence="Certain"),
            make_raw_anomaly(original_text="14 juillet 1789", confidence="À vérifier"),
        ]
        items = make_items_list()
        page_hints = {"Victor Hugo": 0, "14 juillet 1789": 2}
        result = _parse_anomalies(raw, page_hints, items)
        assert len(result) == 2
        assert all(c.confidence == "À vérifier" for c in result)
