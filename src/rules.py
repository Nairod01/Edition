"""
Règles locales de typographie et de grammaire française.
Ces règles sont appliquées par regex AVANT l'appel au LLM,
pour couvrir les cas mécaniques sans consommer de tokens API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator


@dataclass
class RuleMatch:
    """Un problème détecté par une règle locale."""
    rule_id: str
    category: str          # typographie | orthographe | ponctuation | style
    severity: str          # error | warning | suggestion
    text_found: str        # texte exact trouvé
    message: str
    correction: str        # texte corrigé suggéré
    position: int          # offset dans la chaîne analysée
    length: int            # longueur du texte fautif

    @property
    def end_position(self) -> int:
        return self.position + self.length


# ─────────────────────────────────────────────
# Définition des règles
# ─────────────────────────────────────────────

class FrenchTypographyRules:
    """
    Règles typographiques françaises basées sur les recommandations du
    Lexique des règles typographiques en usage à l'Imprimerie nationale.
    """

    RULES: list[dict] = [

        # ── Espaces insécables ──────────────────────────────────────────────

        {
            "id": "typo_espace_point_exclamation",
            "pattern": r"(?<! )(?<!\xa0)!",
            "exclude_pattern": r"^\s*!",  # début de ligne (émoticône, etc.)
            "category": "typographie",
            "severity": "error",
            "message": "Espace insécable manquante avant «\u00a0!»",
            "correction_fn": lambda m: "\u00a0!",
        },
        {
            "id": "typo_espace_point_interrogation",
            "pattern": r"(?<! )(?<!\xa0)\?",
            "category": "typographie",
            "severity": "error",
            "message": "Espace insécable manquante avant «\u00a0?»",
            "correction_fn": lambda m: "\u00a0?",
        },
        {
            "id": "typo_espace_deux_points",
            "pattern": r"(?<=[^\s])(?<!\xa0):(?![/\\])",  # exclut :// (URLs) et :\\ (chemins)
            "category": "typographie",
            "severity": "error",
            "message": "Espace insécable manquante avant «\u00a0:»",
            "correction_fn": lambda m: "\u00a0:",
        },
        {
            "id": "typo_espace_point_virgule",
            "pattern": r"(?<=[^\s])(?<!\xa0);",
            "category": "typographie",
            "severity": "error",
            "message": "Espace insécable manquante avant «\u00a0;»",
            "correction_fn": lambda m: "\u00a0;",
        },
        {
            "id": "typo_espace_apres_guillemet_ouvrant",
            "pattern": r"«(?!\s)(?!\xa0)",
            "category": "typographie",
            "severity": "error",
            "message": "Espace insécable manquante après «\u00a0«»",
            "correction_fn": lambda m: "«\u00a0",
        },
        {
            "id": "typo_espace_avant_guillemet_fermant",
            "pattern": r"(?<!\s)(?<!\xa0)»",
            "category": "typographie",
            "severity": "error",
            "message": "Espace insécable manquante avant «\u00a0»»",
            "correction_fn": lambda m: "\u00a0»",
        },

        # ── Guillemets ──────────────────────────────────────────────────────

        {
            "id": "typo_guillemets_anglais_doubles",
            "pattern": r'"([^"]{1,200})"',
            "category": "typographie",
            "severity": "warning",
            "message": "Guillemets anglais (\") : préférer les guillemets français (« »)",
            "correction_fn": lambda m: f"«\u00a0{m.group(1)}\u00a0»",
        },
        {
            "id": "typo_guillemets_apostrophe_doubles",
            "pattern": r"''([^'']{1,200})''",
            "category": "typographie",
            "severity": "warning",
            "message": "Doubles apostrophes utilisées comme guillemets : préférer «\u00a0»",
            "correction_fn": lambda m: f"«\u00a0{m.group(1)}\u00a0»",
        },

        # ── Points de suspension ────────────────────────────────────────────

        {
            "id": "typo_trois_points",
            "pattern": r"\.{3}",
            "category": "typographie",
            "severity": "warning",
            "message": "Trois points (…) : utiliser le caractère points de suspension «\u00a0…\u00a0»",
            "correction_fn": lambda m: "…",
        },
        {
            "id": "typo_points_suspension_espace",
            "pattern": r"\.\.\.",
            "category": "typographie",
            "severity": "warning",
            "message": "Points de suspension saisis en trois caractères : utiliser «\u00a0…\u00a0»",
            "correction_fn": lambda m: "…",
        },

        # ── Tirets ─────────────────────────────────────────────────────────

        {
            "id": "typo_tiret_dialogue_court",
            "pattern": r"(?m)^-\s",
            "category": "typographie",
            "severity": "warning",
            "message": "Tiret de dialogue court (-) : préférer le tiret cadratin (—)",
            "correction_fn": lambda m: "— ",
        },
        {
            "id": "typo_tiret_incise_court",
            "pattern": r"\s-\s",
            "category": "typographie",
            "severity": "suggestion",
            "message": "Tiret court pour incise : vérifier si tiret cadratin (—) ou demi-cadratin (–) est approprié",
            "correction_fn": lambda m: " — ",
        },

        # ── Espaces multiples et apostrophes ───────────────────────────────

        {
            "id": "typo_espaces_multiples",
            "pattern": r" {2,}",
            "category": "typographie",
            "severity": "error",
            "message": "Espaces multiples consécutives",
            "correction_fn": lambda m: " ",
        },
        {
            "id": "typo_apostrophe_droite",
            "pattern": r"(?<=[a-zàâäéèêëîïôùûüœ])'(?=[a-zàâäéèêëîïôùûüœA-ZÀÂÄÉÈÊËÎÏÔÙÛÜŒ])",
            "category": "typographie",
            "severity": "suggestion",
            "message": "Apostrophe droite (') : préférer l'apostrophe typographique (')",
            "correction_fn": lambda m: "'",
        },

        # ── Majuscules ──────────────────────────────────────────────────────

        {
            "id": "typo_majuscule_apres_point",
            "pattern": r"(?<=[.!?…])\s+(?=[a-zàâäéèêëîïôùûüœ])",
            "category": "orthographe",
            "severity": "warning",
            "message": "Minuscule après ponctuation forte : vérifier si majuscule requise",
            "correction_fn": lambda m: m.group(0),  # pas de correction automatique
        },

        # ── Espaces avant/après ponctuation ────────────────────────────────

        {
            "id": "typo_espace_avant_virgule",
            "pattern": r"\s,",
            "category": "typographie",
            "severity": "error",
            "message": "Espace avant la virgule",
            "correction_fn": lambda m: ",",
        },
        {
            "id": "typo_espace_avant_point",
            "pattern": r"\s\.",
            "category": "typographie",
            "severity": "error",
            "message": "Espace avant le point",
            "correction_fn": lambda m: ".",
        },
        {
            "id": "typo_pas_espace_apres_ponctuation",
            "pattern": r"[,.](?=[^\s\d\n»"')\]…\-])",
            "category": "typographie",
            "severity": "error",
            "message": "Espace manquante après la ponctuation",
            "correction_fn": lambda m: m.group(0)[0] + " ",
        },

        # ── Nombres ─────────────────────────────────────────────────────────

        {
            "id": "typo_espace_milliers",
            "pattern": r"\b(\d{4,})\b",
            "category": "typographie",
            "severity": "suggestion",
            "message": "Grand nombre sans espace de groupement des milliers",
            "correction_fn": lambda m: _format_number(m.group(1)),
        },

        # ── Redondances et pléonasmes courants ─────────────────────────────

        {
            "id": "style_au_jour_daujourdhui",
            "pattern": r"\bau jour d'aujourd'hui\b",
            "category": "style",
            "severity": "warning",
            "message": "Pléonasme : «\u00a0au jour d'aujourd'hui\u00a0» → «\u00a0aujourd'hui\u00a0»",
            "correction_fn": lambda m: "aujourd'hui",
        },
        {
            "id": "style_monter_en_haut",
            "pattern": r"\bmonter en haut\b",
            "category": "style",
            "severity": "warning",
            "message": "Pléonasme : «\u00a0monter en haut\u00a0» → «\u00a0monter\u00a0»",
            "correction_fn": lambda m: "monter",
        },
        {
            "id": "style_descendre_en_bas",
            "pattern": r"\bdescendre en bas\b",
            "category": "style",
            "severity": "warning",
            "message": "Pléonasme : «\u00a0descendre en bas\u00a0» → «\u00a0descendre\u00a0»",
            "correction_fn": lambda m: "descendre",
        },
        {
            "id": "style_reculer_en_arriere",
            "pattern": r"\breculer en arrière\b",
            "category": "style",
            "severity": "warning",
            "message": "Pléonasme : «\u00a0reculer en arrière\u00a0» → «\u00a0reculer\u00a0»",
            "correction_fn": lambda m: "reculer",
        },
        {
            "id": "style_prevision_future",
            "pattern": r"\bprévision[s]? future[s]?\b",
            "category": "style",
            "severity": "warning",
            "message": "Pléonasme : «\u00a0prévision future\u00a0» → «\u00a0prévision\u00a0»",
            "correction_fn": lambda m: "prévision",
        },

        # ── Répétitions proches ─────────────────────────────────────────────

        {
            "id": "style_repetition_que",
            "pattern": r"\bque\b.{0,40}\bque\b",
            "category": "style",
            "severity": "suggestion",
            "message": "Répétition de «\u00a0que\u00a0» : vérifier si reformulation possible",
            "correction_fn": lambda m: m.group(0),
        },

        # ── Conventions de nombres ──────────────────────────────────────────

        {
            "id": "style_nombre_debut_phrase",
            "pattern": r"(?m)(?<=^|\.\s|\?\s|!\s)\d+\b",
            "category": "style",
            "severity": "suggestion",
            "message": "Nombre en chiffre en début de phrase : préférer les lettres",
            "correction_fn": lambda m: m.group(0),
        },
    ]

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._compiled: list[tuple[dict, re.Pattern]] = []
        self._compile_rules()

    def _compile_rules(self):
        for rule in self.RULES:
            try:
                compiled = re.compile(rule["pattern"], re.UNICODE | re.IGNORECASE
                                      if rule.get("case_insensitive", True) else re.UNICODE)
                self._compiled.append((rule, compiled))
            except re.error:
                pass  # règle invalide ignorée silencieusement

    def check(self, text: str) -> list[RuleMatch]:
        """Applique toutes les règles sur un texte et retourne les correspondances."""
        matches: list[RuleMatch] = []

        for rule, pattern in self._compiled:
            for m in pattern.finditer(text):
                # Calcul de la correction
                try:
                    correction = rule["correction_fn"](m)
                except Exception:
                    correction = ""

                matches.append(RuleMatch(
                    rule_id=rule["id"],
                    category=rule["category"],
                    severity=rule["severity"],
                    text_found=m.group(0),
                    message=rule["message"],
                    correction=correction,
                    position=m.start(),
                    length=len(m.group(0)),
                ))

        # Dédoublonnage : si deux règles pointent le même offset, garder la plus grave
        matches = _dedup_matches(matches)
        return sorted(matches, key=lambda r: r.position)

    def check_repetitions_in_paragraph(self, text: str, min_word_len: int = 5, window: int = 200) -> list[RuleMatch]:
        """
        Détecte les répétitions de mots significatifs dans une fenêtre glissante.
        """
        matches: list[RuleMatch] = []
        words = list(re.finditer(r"\b[a-zàâäéèêëîïôùûüœ]{" + str(min_word_len) + r",}\b", text, re.IGNORECASE))
        seen: dict[str, int] = {}

        for word_match in words:
            word = word_match.group(0).lower()
            if word in _STOP_WORDS_FR:
                continue
            pos = word_match.start()
            if word in seen and pos - seen[word] < window:
                matches.append(RuleMatch(
                    rule_id="style_repetition_mot",
                    category="style",
                    severity="suggestion",
                    text_found=word_match.group(0),
                    message=f"Répétition du mot «\u00a0{word}\u00a0» dans un court espace",
                    correction="",
                    position=pos,
                    length=len(word_match.group(0)),
                ))
            seen[word] = pos

        return matches


# ─────────────────────────────────────────────
# Utilitaires
# ─────────────────────────────────────────────

def _format_number(n: str) -> str:
    """Formate un nombre avec espaces de groupement (4 567 890)."""
    return f"{int(n):,}".replace(",", "\u202f")  # espace fine insécable


def _dedup_matches(matches: list[RuleMatch]) -> list[RuleMatch]:
    """Supprime les doublons qui pointent au même endroit."""
    severity_rank = {"error": 0, "warning": 1, "suggestion": 2}
    by_pos: dict[int, RuleMatch] = {}
    for m in matches:
        if m.position not in by_pos:
            by_pos[m.position] = m
        else:
            existing = by_pos[m.position]
            if severity_rank[m.severity] < severity_rank[existing.severity]:
                by_pos[m.position] = m
    return list(by_pos.values())


# Mots vides français (ignorés pour la détection des répétitions)
_STOP_WORDS_FR = {
    "alors", "aussi", "autre", "avec", "avoir", "bien", "celui", "cette",
    "comme", "dans", "depuis", "donc", "dont", "elle", "elles", "encore",
    "entre", "faire", "mais", "même", "moins", "nous", "nous", "onde",
    "plus", "pour", "puis", "quand", "quel", "quelle", "quels", "quelles",
    "sans", "sauf", "selon", "sous", "tant", "telle", "tels", "telles",
    "tout", "toute", "tous", "toutes", "très", "vers", "votre", "vôtre",
    "vous", "voix",
}
