"""
Root conftest.py — shared fixtures and sys.path setup for the backend test suite.
"""
import sys
from pathlib import Path

# Ensure the project root is on sys.path so that `backend.*` imports resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

import pytest
from unittest.mock import MagicMock

from backend.services.pdf_extractor import ExtractionResult, PageText
from backend.services.fact_checker import FactCheckItem


# ── Shared fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def sample_page_text():
    return PageText(
        page_num=0,
        text="Victor Hugo naquit le 26 février 1802 à Besançon.",
        word_count=8,
        blocks=[],
    )


@pytest.fixture
def sample_extraction(sample_page_text):
    page2 = PageText(
        page_num=1,
        text="Marie Curie reçut le prix Nobel en 1903.",
        word_count=8,
        blocks=[],
    )
    return ExtractionResult(
        pages=[sample_page_text, page2],
        total_pages=2,
        total_words=16,
        full_text="[PAGE 1]\nVictor Hugo naquit le 26 février 1802 à Besançon.\n[PAGE 2]\nMarie Curie reçut le prix Nobel en 1903.",
        proper_nouns=["Victor Hugo", "Marie Curie"],
        dates=[
            {"text": "26 février 1802", "context": "Victor Hugo naquit le 26 février 1802 à Besançon.", "page": 0},
            {"text": "1903", "context": "prix Nobel en 1903", "page": 1},
        ],
        defined_terms=[],
        placeholders=[],
        titles=[],
    )


@pytest.fixture
def sample_fact_check_item():
    return FactCheckItem(
        query="Victor Hugo",
        context="Victor Hugo naquit le 26 février 1802 à Besançon.",
        page_num=0,
        original_text="Victor Hugo",
        item_type="proper_noun",
    )


@pytest.fixture
def sample_correction_dict():
    return {
        "page_number": 0,
        "category": "A",
        "original_text": "naquit",
        "corrected_text": "naquit",
        "description": "Test correction",
        "explanation": "Explanation text",
        "source": "",
        "confidence": "Certain",
        "annotation_type": "StrikeOut",
        "color_r": 1.0,
        "color_g": 0.65,
        "color_b": 0.65,
    }


@pytest.fixture
def mock_db_session():
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    return session
