"""
Tests for pure functions and regex patterns in backend/services/pdf_extractor.py.
No real PDF file needed — tests regex and _is_valid_proper_noun only.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

import pytest
import re

from backend.services.pdf_extractor import (
    _is_valid_proper_noun,
    _DATE_WITH_MONTH,
    _DATE_DD_MM_YYYY,
    _PROPER_NOUN_MULTI,
    _PLACEHOLDER_RE,
    _COMMON_WORDS,
)


# ── _is_valid_proper_noun ──────────────────────────────────────────────────────

class TestIsValidProperNoun:

    def test_valid_two_word_name(self):
        assert _is_valid_proper_noun("Victor Hugo") is True

    def test_valid_three_word_name(self):
        assert _is_valid_proper_noun("Charles de Gaulle") is True

    def test_valid_accented_name(self):
        assert _is_valid_proper_noun("Marie Curie") is True

    def test_single_word_rejected(self):
        assert _is_valid_proper_noun("Hugo") is False

    def test_empty_string(self):
        assert _is_valid_proper_noun("") is False

    def test_all_common_words_rejected(self):
        # "Le La" — both in _COMMON_WORDS
        assert _is_valid_proper_noun("Le La") is False

    def test_newline_in_name_rejected(self):
        assert _is_valid_proper_noun("Victor\nHugo") is False

    def test_carriage_return_in_name_rejected(self):
        assert _is_valid_proper_noun("Victor\rHugo") is False

    def test_single_char_lowercase_word_rejected(self):
        # "a" is a single lowercase letter — not an initial
        assert _is_valid_proper_noun("Victor a Hugo") is False

    def test_uppercase_initial_allowed(self):
        # "C. S. Lewis" style — single uppercase letters are OK
        # Note: the function checks `len(w) == 1 and not w.isupper()`
        assert _is_valid_proper_noun("C Lewis") is True

    def test_no_word_with_three_chars(self):
        # "Ab" (2 chars) + "Cd" (2 chars) — neither >= 3, so False
        assert _is_valid_proper_noun("Ab Cd") is False

    def test_one_long_word_passes(self):
        # "Abc" (3) + "D" (1 uppercase) — should pass
        assert _is_valid_proper_noun("Abc Def") is True

    def test_very_long_name(self):
        assert _is_valid_proper_noun("Marie-Anne de Médicis Bonaparte") is True

    def test_mixed_case_valid(self):
        assert _is_valid_proper_noun("Jean-Paul Sartre") is True


# ── _DATE_WITH_MONTH ───────────────────────────────────────────────────────────

class TestDateWithMonth:

    def test_full_french_date(self):
        matches = _DATE_WITH_MONTH.findall("né le 26 février 1802 à Besançon")
        assert len(matches) >= 1
        assert "26 février 1802" in matches[0]

    def test_month_and_year_only(self):
        matches = _DATE_WITH_MONTH.findall("en juillet 1789")
        assert len(matches) >= 1

    def test_english_month(self):
        matches = _DATE_WITH_MONTH.findall("born in January 1900")
        assert len(matches) >= 1

    def test_no_year_not_matched(self):
        matches = _DATE_WITH_MONTH.findall("le 14 juillet prochain")
        assert len(matches) == 0

    def test_case_insensitive(self):
        matches = _DATE_WITH_MONTH.findall("en MARS 1800")
        assert len(matches) >= 1

    def test_all_french_months(self):
        months = [
            "janvier", "février", "mars", "avril", "mai", "juin",
            "juillet", "août", "septembre", "octobre", "novembre", "décembre"
        ]
        for month in months:
            text = f"en {month} 1900"
            matches = _DATE_WITH_MONTH.findall(text)
            assert len(matches) >= 1, f"Month '{month}' not matched"

    def test_bare_year_not_matched(self):
        matches = _DATE_WITH_MONTH.findall("l'an 1789")
        assert len(matches) == 0

    def test_multiple_dates(self):
        text = "né en mars 1802, mort en mai 1885"
        matches = _DATE_WITH_MONTH.findall(text)
        assert len(matches) == 2


# ── _DATE_DD_MM_YYYY ───────────────────────────────────────────────────────────

def _dd_mm_yyyy_matches(text):
    """Comme en prod (pdf_extractor) : finditer + group(0) — findall renverrait
    le groupe capturant du séparateur (backreference), pas la date complète."""
    return [m.group(0) for m in _DATE_DD_MM_YYYY.finditer(text)]


class TestDateDDMMYYYY:

    def test_slash_separator(self):
        matches = _dd_mm_yyyy_matches("14/07/1789")
        assert len(matches) == 1
        assert matches[0] == "14/07/1789"

    def test_dash_separator(self):
        matches = _dd_mm_yyyy_matches("14-07-1789")
        assert len(matches) == 1

    def test_dot_separator(self):
        matches = _dd_mm_yyyy_matches("14.07.1789")
        assert len(matches) == 1

    def test_single_digit_day(self):
        matches = _dd_mm_yyyy_matches("4/7/1789")
        assert len(matches) == 1

    def test_no_match_without_four_digit_year(self):
        matches = _dd_mm_yyyy_matches("14/07/89")
        assert len(matches) == 0

    def test_no_match_for_plain_text(self):
        matches = _dd_mm_yyyy_matches("hello world")
        assert len(matches) == 0

    def test_multiple_dates_in_text(self):
        text = "from 01/01/2000 to 31/12/2023"
        matches = _dd_mm_yyyy_matches(text)
        assert len(matches) == 2

    def test_mixed_separators_not_matched(self):
        # Separator must be consistent within one date
        matches = _dd_mm_yyyy_matches("14/07-1789")
        assert len(matches) == 0


# ── _PROPER_NOUN_MULTI ─────────────────────────────────────────────────────────

class TestProperNounMulti:

    def test_two_capitalized_words(self):
        matches = _PROPER_NOUN_MULTI.findall("Victor Hugo était là")
        assert any("Victor Hugo" in m for m in matches)

    def test_three_capitalized_words(self):
        matches = _PROPER_NOUN_MULTI.findall("Marie de Médicis régnait")
        # "Marie de Médicis" should appear
        found = " ".join([m if isinstance(m, str) else m[0] for m in matches])
        assert "Marie" in found

    def test_single_capitalized_word_not_enough(self):
        # Pattern requires at least 2 capitalized words
        text = "Hugo était là"
        matches = _PROPER_NOUN_MULTI.findall(text)
        # "Hugo" alone should not match (needs at least 2)
        simple_matches = [m for m in matches if m.strip() == "Hugo"]
        assert len(simple_matches) == 0

    def test_lowercase_words_not_matched(self):
        matches = _PROPER_NOUN_MULTI.findall("hello world")
        assert len(matches) == 0

    def test_accented_capitals_matched(self):
        matches = _PROPER_NOUN_MULTI.findall("Émile Zola écrivit")
        assert len(matches) >= 1

    def test_sentence_start_not_confused(self):
        # "La grande" should not create a proper noun
        matches = _PROPER_NOUN_MULTI.findall("La grande maison")
        # "La grande" — "grande" is lowercase, not capitalized → should not match
        assert len(matches) == 0


# ── _PLACEHOLDER_RE ───────────────────────────────────────────────────────────

class TestPlaceholderRe:

    def test_xx_placeholder(self):
        matches = _PLACEHOLDER_RE.findall("Voir la figure XX")
        assert len(matches) >= 1

    def test_xxx_placeholder(self):
        matches = _PLACEHOLDER_RE.findall("Auteur XXX inconnu")
        assert len(matches) >= 1

    def test_xxxxx_placeholder(self):
        matches = _PLACEHOLDER_RE.findall("complété par Xxxxx")
        assert len(matches) >= 1

    def test_tbd_placeholder(self):
        matches = _PLACEHOLDER_RE.findall("Date: TBD")
        assert len(matches) >= 1

    def test_todo_placeholder(self):
        matches = _PLACEHOLDER_RE.findall("Section TODO non complétée")
        assert len(matches) >= 1

    def test_a_completer_placeholder(self):
        matches = _PLACEHOLDER_RE.findall("Insérer À COMPLÉTER ici")
        assert len(matches) >= 1

    def test_na_placeholder(self):
        matches = _PLACEHOLDER_RE.findall("Résultat: N.A.")
        assert len(matches) >= 1

    def test_illu_placeholder(self):
        matches = _PLACEHOLDER_RE.findall("ILLU123 à insérer")
        assert len(matches) >= 1

    def test_normal_text_not_matched(self):
        matches = _PLACEHOLDER_RE.findall("Victor Hugo naquit en 1802")
        assert len(matches) == 0

    def test_case_insensitive(self):
        matches = _PLACEHOLDER_RE.findall("voir tbd pour les détails")
        assert len(matches) >= 1

    def test_xx_dash_xx(self):
        matches = _PLACEHOLDER_RE.findall("pages XX-XX")
        assert len(matches) >= 1
