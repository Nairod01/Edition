"""
Moteur d'analyse éditoriale via Claude API.
Effectue plusieurs passes ciblées (grammaire, style, cohérence, structure)
et retourne des issues structurées avec leur position dans le texte.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import anthropic

from .extractor import DocumentStructure, TextBlock
from .rules import FrenchTypographyRules, RuleMatch

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Structure d'un problème éditorial
# ─────────────────────────────────────────────

@dataclass
class EditorialIssue:
    """Représente un problème éditorial détecté, localisé dans le PDF."""

    # Localisation
    page_num: int           # 1-indexé pour affichage
    text_snippet: str       # texte exact à rechercher dans le PDF pour placer l'annotation
    context: str            # phrase ou paragraphe entier (pour confirmer l'emplacement)

    # Classification
    category: str           # orthographe | typographie | style | homogeneisation | structure | maquette
    severity: str           # error | warning | suggestion
    source: str             # "local_rule" | "claude_grammar" | "claude_style" | "claude_coherence" | "claude_structure"

    # Message éditorial
    message: str            # explication professionnelle du problème
    correction: str = ""    # suggestion de correction (vide si pas de correction unique)
    rule_id: str = ""       # ID de la règle (si règle locale)

    # Couleur d'annotation (RGB 0-1)
    @property
    def annotation_color(self) -> tuple[float, float, float]:
        return _CATEGORY_COLORS.get(self.category, (0.5, 0.5, 0.5))


_CATEGORY_COLORS: dict[str, tuple[float, float, float]] = {
    "orthographe":      (0.90, 0.10, 0.10),  # rouge
    "typographie":      (1.00, 0.50, 0.00),  # orange
    "style":            (0.95, 0.80, 0.00),  # jaune
    "homogeneisation":  (0.10, 0.50, 0.90),  # bleu
    "structure":        (0.55, 0.10, 0.80),  # violet
    "maquette":         (0.20, 0.70, 0.30),  # vert
}


# ─────────────────────────────────────────────
# Prompts système par type d'analyse
# ─────────────────────────────────────────────

_SYSTEM_EDITOR = """Tu es une éditrice professionnelle française avec 20 ans d'expérience dans l'édition littéraire.
Tu maîtrises parfaitement :
- Les règles grammaticales et orthographiques du français
- La typographie française (Lexique de l'Imprimerie nationale)
- Les styles narratifs (roman, essai, documentaire)
- La continuité narrative et la cohérence d'ensemble
- Le rythme et la fluidité de la prose

Tu analyses des manuscrits avec exigence et bienveillance. Tes remarques sont précises, professionnelles et actionnables.
Tu identifies toujours le texte EXACT concerné pour permettre sa localisation."""


_PROMPT_GRAMMAR = """Analyse ce passage de manuscrit et identifie TOUS les problèmes de grammaire et d'orthographe.

PASSAGE (pages {start_page}–{end_page}) :
{text}

Réponds UNIQUEMENT avec un objet JSON valide, sans texte avant ni après :
{{
  "issues": [
    {{
      "text_snippet": "texte exact fautif (15 mots max)",
      "context": "phrase complète contenant le problème",
      "category": "orthographe",
      "severity": "error",
      "message": "Explication précise du problème",
      "correction": "version corrigée du snippet"
    }}
  ]
}}

Catégories autorisées : "orthographe"
Sévérités : "error" (faute claire), "warning" (problème probable), "suggestion" (amélioration)
Si aucun problème, retourne {{"issues": []}}"""


_PROMPT_STYLE = """Analyse ce passage en tant qu'éditrice littéraire et identifie les problèmes de style et de lisibilité.

Cherche :
- Répétitions de mots ou de structures dans un même paragraphe
- Phrases trop longues ou syntaxe lourde (> 50 mots sans ponctuation forte)
- Tournures passives excessives
- Abus de nominalisations
- Adverbes en -ment redondants
- Clichés ou formules figées
- Ruptures de registre (mélange soutenu/familier non intentionnel)

PASSAGE (pages {start_page}–{end_page}) :
{text}

Réponds UNIQUEMENT avec un objet JSON valide :
{{
  "issues": [
    {{
      "text_snippet": "texte exact concerné (15 mots max)",
      "context": "phrase ou paragraphe contenant le problème",
      "category": "style",
      "severity": "warning",
      "message": "Explication éditoriale du problème",
      "correction": "suggestion de reformulation (si applicable)"
    }}
  ]
}}"""


_PROMPT_COHERENCE = """Analyse la cohérence et l'homogénéisation de ce texte.

Cherche :
- Incohérences dans les noms propres (variantes graphiques d'un même nom)
- Variations de vouvoiement/tutoiement non justifiées
- Changements de temps narratif non intentionnels (passé simple ↔ imparfait ↔ présent)
- Style de dialogue incohérent (tirets vs guillemets)
- Termes techniques ou jargon utilisés de façon variable pour le même concept
- Continuité narrative (un personnage change de lieu ou d'état sans explication)
- Ruptures de point de vue (POV) non signalées

TEXTE COMPLET (résumé par page) :
{text}

NOMS PROPRES DÉJÀ IDENTIFIÉS : {known_entities}

Réponds UNIQUEMENT avec un objet JSON valide :
{{
  "issues": [
    {{
      "text_snippet": "texte exact concerné",
      "context": "phrase contenant le problème",
      "page_num": 1,
      "category": "homogeneisation",
      "severity": "warning",
      "message": "Description précise de l'incohérence",
      "correction": "suggestion"
    }}
  ],
  "entities_detected": {{
    "personnages": ["Nom1", "Nom2"],
    "lieux": ["Lieu1"],
    "termes_specifiques": ["terme1"]
  }}
}}"""


_PROMPT_STRUCTURE = """Analyse la structure narrative et éditoriale de ce texte.

Cherche :
- Déséquilibres importants entre chapitres (longueur, rythme)
- Transitions abruptes entre scènes sans indication temporelle ou spatiale
- Analepses (retours arrière) non signalées
- Prolepses (anticipations) confuses
- Chapitres ou sections sans clôture satisfaisante
- Répétitions d'information (même fait expliqué deux fois)
- Informations contradictoires entre passages

STRUCTURE DU DOCUMENT :
{structure_summary}

Réponds UNIQUEMENT avec un objet JSON valide :
{{
  "issues": [
    {{
      "text_snippet": "texte de référence (début du passage concerné)",
      "context": "description du problème structurel",
      "page_num": 1,
      "category": "structure",
      "severity": "warning",
      "message": "Description précise du problème structural",
      "correction": "suggestion éditoriale"
    }}
  ]
}}"""


_PROMPT_LAYOUT = """Analyse la mise en page et le formatage de ce document.

Cherche :
- Incohérences dans les niveaux de titres (hiérarchie)
- Numérotation de chapitres erratique
- Inconsistances dans les en-têtes et pieds de page
- Espacements anormaux
- Coupure de paragraphes en fin de page laissant une ligne seule (veuve/orpheline)
- Capitalisation inconsistante des titres

STRUCTURE DÉTECTÉE :
{structure_summary}

Réponds UNIQUEMENT avec un objet JSON valide :
{{
  "issues": [
    {{
      "text_snippet": "titre ou texte concerné",
      "context": "description du problème",
      "page_num": 1,
      "category": "maquette",
      "severity": "warning",
      "message": "Description du problème de mise en page",
      "correction": "suggestion"
    }}
  ]
}}"""


# ─────────────────────────────────────────────
# Analyseur principal
# ─────────────────────────────────────────────

class EditorialAnalyzer:
    """
    Orchestre les analyses locales (règles) et LLM (Claude)
    pour produire la liste complète des issues éditoriales.
    """

    def __init__(self, config: dict, api_key: str | None = None):
        self.config = config
        self.analyses_config = config.get("analyses", {})
        self.model = config.get("modele", "claude-opus-4-6")
        self.max_parallel = config.get("max_requetes_paralleles", 3)
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.rules_engine = FrenchTypographyRules(config)

    def analyze(self, structure: DocumentStructure, chunks: list[dict]) -> list[EditorialIssue]:
        """Lance toutes les passes d'analyse et consolide les résultats."""
        all_issues: list[EditorialIssue] = []

        # Passe 1 : règles locales (instantané, sans API)
        if self.analyses_config.get("typographie", True):
            local_issues = self._run_local_rules(structure)
            all_issues.extend(local_issues)
            logger.info(f"Règles locales : {len(local_issues)} problèmes détectés")

        # Passes LLM (async pour parallélisme)
        llm_issues = asyncio.run(self._run_llm_analyses(structure, chunks))
        all_issues.extend(llm_issues)

        # Dédoublonnage global
        all_issues = self._deduplicate(all_issues)

        logger.info(f"Total : {len(all_issues)} problèmes après dédoublonnage")
        return all_issues

    # ── Règles locales ───────────────────────

    def _run_local_rules(self, structure: DocumentStructure) -> list[EditorialIssue]:
        issues = []
        known_names = set(self.config.get("noms_propres", []))

        for page in structure.pages:
            if page.is_scanned:
                continue
            for block in page.blocks:
                if block.block_type not in ("paragraph", "footnote", "title", "subtitle"):
                    continue

                text = block.full_text
                if not text.strip():
                    continue

                # Règles typographiques
                for match in self.rules_engine.check(text):
                    # Ignorer si le texte touche un nom propre connu
                    if any(name in match.text_found for name in known_names):
                        continue
                    issues.append(EditorialIssue(
                        page_num=block.page_num + 1,
                        text_snippet=match.text_found[:80],
                        context=text[:200],
                        category=match.category,
                        severity=match.severity,
                        source="local_rule",
                        message=match.message,
                        correction=match.correction,
                        rule_id=match.rule_id,
                    ))

                # Répétitions dans le paragraphe
                if block.block_type == "paragraph":
                    for match in self.rules_engine.check_repetitions_in_paragraph(text):
                        issues.append(EditorialIssue(
                            page_num=block.page_num + 1,
                            text_snippet=match.text_found[:80],
                            context=text[:200],
                            category="style",
                            severity="suggestion",
                            source="local_rule",
                            message=match.message,
                            correction="",
                            rule_id=match.rule_id,
                        ))

        return issues

    # ── Analyses LLM ────────────────────────

    async def _run_llm_analyses(self, structure: DocumentStructure, chunks: list[dict]) -> list[EditorialIssue]:
        issues: list[EditorialIssue] = []
        tasks = []

        semaphore = asyncio.Semaphore(self.max_parallel)

        # Grammaire + Style : par chunk
        if self.analyses_config.get("orthographe_grammaire", True):
            for chunk in chunks:
                tasks.append(self._analyze_chunk(semaphore, chunk, "grammar"))

        if self.analyses_config.get("style_lisibilite", True):
            for chunk in chunks:
                tasks.append(self._analyze_chunk(semaphore, chunk, "style"))

        # Cohérence : sur le texte complet (résumé)
        if self.analyses_config.get("homogeneisation", True):
            tasks.append(self._analyze_coherence(semaphore, structure))

        # Structure : sur la structure globale
        if self.analyses_config.get("structure_narrative", True):
            tasks.append(self._analyze_structure_global(semaphore, structure))

        # Maquette
        if self.analyses_config.get("maquette", True):
            tasks.append(self._analyze_layout(semaphore, structure))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Erreur lors d'une analyse LLM : {result}")
            elif result:
                issues.extend(result)

        return issues

    async def _analyze_chunk(self, semaphore: asyncio.Semaphore, chunk: dict, analysis_type: str) -> list[EditorialIssue]:
        async with semaphore:
            prompt_template = _PROMPT_GRAMMAR if analysis_type == "grammar" else _PROMPT_STYLE
            prompt = prompt_template.format(
                start_page=chunk["start_page"],
                end_page=chunk["end_page"],
                text=chunk["text"][:6000],  # sécurité : limite de taille
            )
            try:
                raw = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._call_claude(prompt)
                )
                return self._parse_issues(raw, chunk["start_page"],
                                          source=f"claude_{analysis_type}")
            except Exception as e:
                logger.warning(f"Chunk p.{chunk['start_page']}-{chunk['end_page']} [{analysis_type}] : {e}")
                return []

    async def _analyze_coherence(self, semaphore: asyncio.Semaphore, structure: DocumentStructure) -> list[EditorialIssue]:
        async with semaphore:
            # Résumé du document pour l'analyse de cohérence
            text_summary = self._build_coherence_summary(structure)
            known_entities = json.dumps(
                {"noms_propres": self.config.get("noms_propres", [])},
                ensure_ascii=False
            )
            prompt = _PROMPT_COHERENCE.format(
                text=text_summary[:8000],
                known_entities=known_entities,
            )
            try:
                raw = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._call_claude(prompt)
                )
                issues = self._parse_issues(raw, 1, source="claude_coherence")

                # Enrichir la structure avec les entités détectées
                try:
                    data = json.loads(raw)
                    if "entities_detected" in data:
                        structure.entities = data["entities_detected"]
                except Exception:
                    pass

                return issues
            except Exception as e:
                logger.warning(f"Analyse cohérence : {e}")
                return []

    async def _analyze_structure_global(self, semaphore: asyncio.Semaphore, structure: DocumentStructure) -> list[EditorialIssue]:
        async with semaphore:
            summary = self._build_structure_summary(structure)
            prompt = _PROMPT_STRUCTURE.format(structure_summary=summary[:6000])
            try:
                raw = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._call_claude(prompt)
                )
                return self._parse_issues(raw, 1, source="claude_structure")
            except Exception as e:
                logger.warning(f"Analyse structure : {e}")
                return []

    async def _analyze_layout(self, semaphore: asyncio.Semaphore, structure: DocumentStructure) -> list[EditorialIssue]:
        async with semaphore:
            summary = self._build_layout_summary(structure)
            prompt = _PROMPT_LAYOUT.format(structure_summary=summary[:4000])
            try:
                raw = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._call_claude(prompt)
                )
                return self._parse_issues(raw, 1, source="claude_layout")
            except Exception as e:
                logger.warning(f"Analyse maquette : {e}")
                return []

    # ── Appel Claude ─────────────────────────

    def _call_claude(self, prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=_SYSTEM_EDITOR,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    # ── Parsing des réponses JSON ─────────────

    def _parse_issues(self, raw_json: str, default_page: int, source: str) -> list[EditorialIssue]:
        """Parse la réponse JSON de Claude en liste d'EditorialIssue."""
        issues = []

        # Extraction du JSON même si Claude ajoute du texte parasite
        json_str = _extract_json(raw_json)
        if not json_str:
            logger.debug(f"Pas de JSON valide dans la réponse : {raw_json[:200]}")
            return issues

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.debug(f"Erreur JSON ({e}) dans : {json_str[:200]}")
            return issues

        for item in data.get("issues", []):
            if not item.get("text_snippet") or not item.get("message"):
                continue
            try:
                issues.append(EditorialIssue(
                    page_num=int(item.get("page_num", default_page)),
                    text_snippet=str(item["text_snippet"])[:200],
                    context=str(item.get("context", ""))[:400],
                    category=str(item.get("category", "style")),
                    severity=str(item.get("severity", "warning")),
                    source=source,
                    message=str(item["message"]),
                    correction=str(item.get("correction", "")),
                ))
            except (ValueError, TypeError):
                continue

        return issues

    # ── Résumés pour les analyses globales ───

    def _build_coherence_summary(self, structure: DocumentStructure) -> str:
        """Construit un résumé lisible du document pour l'analyse de cohérence."""
        lines = [f"Document : {structure.title}", f"Pages : {structure.page_count}", ""]
        for page in structure.pages:
            body_texts = [b.full_text for b in page.blocks if b.block_type == "paragraph" and b.full_text.strip()]
            titles = [b.full_text for b in page.blocks if b.block_type in ("title", "subtitle") and b.full_text.strip()]
            if titles:
                lines.append(f"\n[Page {page.page_num + 1} — {' / '.join(titles)}]")
            elif body_texts:
                lines.append(f"\n[Page {page.page_num + 1}]")
            for t in body_texts[:3]:  # max 3 paragraphes par page dans le résumé
                lines.append(t[:300])
        return "\n".join(lines)

    def _build_structure_summary(self, structure: DocumentStructure) -> str:
        """Résumé de la structure pour l'analyse structurelle."""
        lines = [
            f"Titre : {structure.title}",
            f"Pages : {structure.page_count}",
            f"Chapitres détectés aux pages : {structure.chapter_pages}",
            f"Notes de bas de page : {'oui' if structure.has_footnotes else 'non'}",
            f"En-têtes/pieds de page : {'oui' if structure.has_headers_footers else 'non'}",
            f"Multi-colonnes : {'oui' if structure.is_multicolumn else 'non'}",
            "",
        ]
        for page in structure.pages:
            titles = [b.full_text for b in page.blocks if b.block_type in ("title", "subtitle")]
            if titles:
                lines.append(f"Page {page.page_num + 1} — Titre : {' | '.join(titles)}")
            body_count = sum(1 for b in page.blocks if b.block_type == "paragraph")
            lines.append(f"  Paragraphes : {body_count}")
        return "\n".join(lines)

    def _build_layout_summary(self, structure: DocumentStructure) -> str:
        """Résumé du formatage pour l'analyse maquette."""
        lines = [
            f"Police corps : taille {structure.dominant_font_size}pt",
            f"Tailles de titres : {structure.title_font_sizes}",
            f"Multi-colonnes : {structure.is_multicolumn}",
            "",
        ]
        for page in structure.pages:
            block_types = [b.block_type for b in page.blocks]
            titles = [(b.full_text[:60], b.font_size) for b in page.blocks if b.block_type in ("title", "subtitle")]
            if titles or "header" in block_types or "footer" in block_types:
                lines.append(f"Page {page.page_num + 1} : {block_types} — titres : {titles}")
        return "\n".join(lines)

    # ── Dédoublonnage ────────────────────────

    def _deduplicate(self, issues: list[EditorialIssue]) -> list[EditorialIssue]:
        """Supprime les doublons : même snippet + même catégorie + même page."""
        seen: set[tuple] = set()
        unique: list[EditorialIssue] = []
        for issue in issues:
            key = (issue.page_num, issue.text_snippet[:40].strip(), issue.category)
            if key not in seen:
                seen.add(key)
                unique.append(issue)
        return unique


# ─────────────────────────────────────────────
# Utilitaires
# ─────────────────────────────────────────────

def _extract_json(text: str) -> str | None:
    """Extrait le premier objet JSON valide d'un texte potentiellement bruité."""
    # Essai direct
    text = text.strip()
    if text.startswith("{"):
        return text

    # Cherche la première accolade ouvrante
    start = text.find("{")
    if start == -1:
        return None

    # Cherche la dernière accolade fermante
    end = text.rfind("}")
    if end == -1 or end < start:
        return None

    return text[start:end + 1]
