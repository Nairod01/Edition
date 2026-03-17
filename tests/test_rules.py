"""
Tests des règles typographiques et stylistiques françaises.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.rules import FrenchTypographyRules


@pytest.fixture
def rules():
    return FrenchTypographyRules()


# ── Espaces insécables ──────────────────────────────────────────────

class TestEspacesInsecables:

    def test_espace_avant_point_exclamation(self, rules):
        matches = rules.check("Bonjour!")
        ids = [m.rule_id for m in matches]
        assert "typo_espace_point_exclamation" in ids

    def test_espace_avant_point_exclamation_ok(self, rules):
        matches = rules.check("Bonjour\u00a0!")
        ids = [m.rule_id for m in matches]
        assert "typo_espace_point_exclamation" not in ids

    def test_espace_avant_point_interrogation(self, rules):
        matches = rules.check("Comment allez-vous?")
        ids = [m.rule_id for m in matches]
        assert "typo_espace_point_interrogation" in ids

    def test_espace_avant_deux_points(self, rules):
        matches = rules.check("Il dit: bonjour")
        ids = [m.rule_id for m in matches]
        assert "typo_espace_deux_points" in ids

    def test_espace_avant_deux_points_url_ok(self, rules):
        # Les URLs ne doivent pas être signalées
        matches = rules.check("Voir https://exemple.com")
        ids = [m.rule_id for m in matches]
        assert "typo_espace_deux_points" not in ids

    def test_espace_avant_point_virgule(self, rules):
        matches = rules.check("Il venait; elle partait")
        ids = [m.rule_id for m in matches]
        assert "typo_espace_point_virgule" in ids

    def test_espace_apres_guillemet_ouvrant(self, rules):
        matches = rules.check("«Bonjour»")
        ids = [m.rule_id for m in matches]
        assert "typo_espace_apres_guillemet_ouvrant" in ids

    def test_espace_avant_guillemet_fermant(self, rules):
        matches = rules.check("«\u00a0Bonjour»")
        ids = [m.rule_id for m in matches]
        assert "typo_espace_avant_guillemet_fermant" in ids

    def test_guillemets_corrects_ok(self, rules):
        matches = rules.check("«\u00a0Bonjour\u00a0»")
        typo_ids = [m.rule_id for m in matches if m.rule_id.startswith("typo_espace")]
        assert not typo_ids


# ── Guillemets ──────────────────────────────────────────────────────

class TestGuillemets:

    def test_guillemets_anglais_detectes(self, rules):
        matches = rules.check('"Bonjour, comment vas-tu ?"')
        ids = [m.rule_id for m in matches]
        assert "typo_guillemets_anglais_doubles" in ids

    def test_correction_guillemets(self, rules):
        matches = rules.check('"test"')
        for m in matches:
            if m.rule_id == "typo_guillemets_anglais_doubles":
                assert "«" in m.correction
                assert "»" in m.correction


# ── Points de suspension ───────────────────────────────────────────

class TestPointsDeSuspension:

    def test_trois_points_detectes(self, rules):
        matches = rules.check("Il hésita...")
        ids = [m.rule_id for m in matches]
        assert "typo_trois_points" in ids or "typo_points_suspension_espace" in ids

    def test_points_suspension_correction(self, rules):
        matches = rules.check("Il hésita...")
        for m in matches:
            if "points" in m.rule_id:
                assert m.correction == "…"


# ── Espaces parasites ──────────────────────────────────────────────

class TestEspacesParasites:

    def test_espaces_multiples(self, rules):
        matches = rules.check("Il  vint  ici")
        ids = [m.rule_id for m in matches]
        assert "typo_espaces_multiples" in ids

    def test_espace_avant_virgule(self, rules):
        matches = rules.check("Bonjour ,comment vas-tu")
        ids = [m.rule_id for m in matches]
        assert "typo_espace_avant_virgule" in ids

    def test_espace_avant_point(self, rules):
        matches = rules.check("C'est fini .")
        ids = [m.rule_id for m in matches]
        assert "typo_espace_avant_point" in ids


# ── Pléonasmes ──────────────────────────────────────────────────────

class TestPleonasmes:

    def test_au_jour_daujourdhui(self, rules):
        matches = rules.check("Au jour d'aujourd'hui, les choses changent.")
        ids = [m.rule_id for m in matches]
        assert "style_au_jour_daujourdhui" in ids

    def test_monter_en_haut(self, rules):
        matches = rules.check("Elle monta en haut de la tour.")
        ids = [m.rule_id for m in matches]
        assert "style_monter_en_haut" in ids

    def test_descendre_en_bas(self, rules):
        matches = rules.check("Il descendit en bas.")
        ids = [m.rule_id for m in matches]
        assert "style_descendre_en_bas" in ids


# ── Répétitions ─────────────────────────────────────────────────────

class TestRepetitions:

    def test_repetition_detectee(self, rules):
        # Même mot significatif deux fois dans une fenêtre courte
        matches = rules.check_repetitions_in_paragraph(
            "La maison était grande. La maison dominait la colline.",
            min_word_len=5,
            window=100,
        )
        texts = [m.text_found.lower() for m in matches]
        assert "maison" in texts

    def test_mots_courts_ignores(self, rules):
        # Les mots trop courts ne sont pas détectés comme répétitions
        matches = rules.check_repetitions_in_paragraph(
            "Il est là. Il est ici.",
            min_word_len=5,
        )
        assert len(matches) == 0

    def test_mots_vides_ignores(self, rules):
        # Les mots vides français ne doivent pas être détectés
        matches = rules.check_repetitions_in_paragraph(
            "Alors il partit, alors elle resta.",
            min_word_len=4,
        )
        texts = [m.text_found.lower() for m in matches]
        assert "alors" not in texts


# ── Tirets ──────────────────────────────────────────────────────────

class TestTirets:

    def test_tiret_dialogue_court(self, rules):
        matches = rules.check("- Bonjour, dit-il.")
        ids = [m.rule_id for m in matches]
        assert "typo_tiret_dialogue_court" in ids

    def test_tiret_cadratin_ok(self, rules):
        matches = rules.check("— Bonjour, dit-il.")
        ids = [m.rule_id for m in matches]
        assert "typo_tiret_dialogue_court" not in ids
