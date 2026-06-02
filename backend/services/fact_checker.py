"""
Vérification des faits (catégorie H) — pilotée par Claude + web_search natif Anthropic.

Architecture :
  - Claude reçoit des groupes de 4 items à vérifier (noms propres, dates, titres d'œuvres).
  - Claude utilise le server tool web_search_20250305 pour effectuer ses propres recherches.
  - Claude interprète les résultats et appelle soumettre_anomalies_H uniquement pour les
    anomalies confirmées (Certain ou Probable).
  - Groupes de 4 items par appel Claude, max 5 appels concurrents (asyncio.Semaphore).
  - Max 5 web searches par groupe (contrôlé par max_uses dans le tool spec).
  - Règle stricte : silence en cas de doute — aucun faux positif.

Compatibilité :
  - Dataclasses FactCheckItem et FactCheckCorrection compatibles avec pipeline.py.
  - check_facts() remplace directement l'ancienne implémentation Tavily.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import anthropic

from backend.config import settings

logger = logging.getLogger(__name__)

# ── Constantes ─────────────────────────────────────────────────────────────────

GROUP_SIZE = 4          # Nombre d'items par appel Claude
MAX_CONCURRENCY = 5     # Semaphore — appels Claude simultanés
MAX_TURNS = 10          # Boucle de conversation multi-tours max
MIN_ITEM_LENGTH = 4     # Items trop courts ignorés

# ── Config catégorie H (utilisée par pipeline.py) ─────────────────────────────

CATEGORY_H_CONFIG = {
    "name": "H – Vérification des faits",
    "color": (1.00, 0.92, 0.40),
    "annotation_type": "Highlight",
}

# ── Dataclasses publiques ──────────────────────────────────────────────────────


@dataclass
class FactCheckItem:
    """Élément à vérifier transmis par Claude (passe précédente du pipeline)."""
    query: str
    context: str
    page_num: int
    original_text: str
    item_type: str  # "date" | "proper_noun" | "title"


@dataclass
class FactCheckCorrection:
    """Anomalie factuelle confirmée — remplace TavilyCorrection."""
    page_num: int
    category: str           # Toujours "H"
    original_text: str
    corrected_text: str | None
    description: str
    explanation: str
    source: str
    confidence: str         # "Certain" | "Probable" | "À vérifier"


# ── Définition des outils Claude ───────────────────────────────────────────────

WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 5,
}

REPORTER_TOOL = {
    "name": "soumettre_anomalies_H",
    "description": "Soumet les anomalies factuelles confirmées par recherche web.",
    "input_schema": {
        "type": "object",
        "properties": {
            "anomalies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "original_text": {
                            "type": "string",
                            "description": "Texte exact du document",
                        },
                        "corrected_text": {
                            "type": "string",
                            "description": "Forme correcte trouvée",
                        },
                        "page_hint": {"type": "integer"},
                        "explanation": {
                            "type": "string",
                            "description": "Explication concise (max 300 cars)",
                        },
                        "source": {
                            "type": "string",
                            "description": "URL ou nom de la source",
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["Certain", "Probable", "À vérifier"],
                        },
                    },
                    "required": [
                        "original_text",
                        "page_hint",
                        "explanation",
                        "source",
                        "confidence",
                    ],
                },
            }
        },
        "required": ["anomalies"],
    },
}

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
Tu es un vérificateur de faits éditorial expert pour des textes en français.

Tu reçois des éléments à vérifier issus d'un document. Utilise web_search pour vérifier.

══════════════════════════════════════════════════════
 CE QUE H PEUT SIGNALER — LISTE BLANCHE STRICTE
══════════════════════════════════════════════════════
H ne signale UNE anomalie QUE si elle appartient à l'une de ces 3 catégories ET que l'erreur est prouvée :

  1. DATE HISTORIQUE AVEC ERREUR D'ANNÉE
     → Ex : "Waterloo (1814)" alors que c'est 1815.
     → Uniquement si une source fiable (Wikipedia, BNF) donne explicitement l'année correcte.
     → JAMAIS pour des dates de composition poétique ou littéraire (voir règle 4 ci-dessous).

  2. TITRE D'ŒUVRE AVEC FAUTE DE FRAPPE MANIFESTE
     → Ex : "Les Misérable" au lieu de "Les Misérables".
     → Uniquement si le titre exact est trouvé sur une source éditeur officiel ou BNF.

  3. NOM PROPRE HISTORIQUE NOTOIRE MAL ORTHOGRAPHIÉ
     → Ex : "Napoléon Bonapart" (manque un e), "Shakespear" (manque un e).
     → Uniquement pour des personnages historiques universellement connus.
     → La faute doit être dans l'orthographe du nom lui-même, pas dans sa mise en contexte.

══════════════════════════════════════════════════════
 CE QUE H NE SIGNALE JAMAIS — LISTE NOIRE ABSOLUE
══════════════════════════════════════════════════════
• Noms de personnages fictifs ou régionaux (même peu connus)
• Noms de lieux géographiques locaux, rues, quartiers
• Noms d'auteurs, d'artistes, d'éditeurs
• Titres honorifiques, fonctions, grades militaires
• Dates de composition poétique ou de rédaction littéraire
• Toute information déjà vérifiable sans ambiguïté dans le document
• Tout élément couvert par A/B/C/D/E/F/G
• Toute vérification dont le résultat web est ambigu ou partiel

RÈGLE ABSOLUE 1 — DOUTE = SILENCE.
Un silence professionnel est préférable à un faux positif.

RÈGLE ABSOLUE 2 — DATES POÉTIQUES ET LITTÉRAIRES.
Une date en fin de poème (ex : « Mars 1870 », « juillet 1836 ») est la date de composition
de CE TEXTE PRÉCIS. Ne jamais comparer avec une autre œuvre du même auteur ou recueil.
Si tu ne trouves pas la date exacte DE CE TEXTE (pas d'un autre), → silence.

RÈGLE ABSOLUE 3 — ANTI-CONTRADICTION.
Si ta source confirme ou est compatible avec ce que dit le document → NE PAS soumettre.
Relire ta source avant de soumettre. Si la source dit la même chose → silence.

RÈGLE ABSOLUE 4 — ORIGINAL ≠ CORRECTION.
Ne soumettre que si original_text ≠ corrected_text. Sinon → silence.

RÈGLE ABSOLUE 5 — CONFIDENCE.
Toutes les anomalies soumises ont obligatoirement confidence = "À vérifier".
Jamais "Certain", jamais "Probable" — l'éditeur humain valide en dernier ressort.

Après tes recherches, appelle soumettre_anomalies_H avec UNIQUEMENT les anomalies prouvées.
Si tout est correct → appelle soumettre_anomalies_H avec une liste vide.\
"""

# ── Filtrage des items ─────────────────────────────────────────────────────────


def _is_valid(text: str) -> bool:
    """Écarte les items trop courts ou sans contenu lexical."""
    text = text.strip()
    if len(text) < MIN_ITEM_LENGTH:
        return False
    # Uniquement des chiffres ou des codes alphanumériques courts sans lettre
    import re
    if re.match(r"^\d+$", text):
        return False
    if not re.search(r"[a-zA-ZÀ-ÿ]", text):
        return False
    return True


def _format_items_for_prompt(items: list[FactCheckItem]) -> str:
    """Formate la liste d'items en bloc lisible pour Claude."""
    lines: list[str] = ["Éléments à vérifier :"]
    type_labels = {
        "date": "Date historique",
        "proper_noun": "Nom propre",
        "title": "Titre d'œuvre",
    }
    for i, item in enumerate(items, start=1):
        label = type_labels.get(item.item_type, "Élément")
        lines.append(
            f"\n{i}. [{label}] « {item.original_text} » (page {item.page_num + 1})"
        )
        if item.context and item.context.strip() != item.original_text.strip():
            lines.append(f"   Contexte : {item.context[:200]}")
    return "\n".join(lines)


# ── Conversation multi-tours avec Claude ───────────────────────────────────────


async def _check_group(
    client: anthropic.AsyncAnthropic,
    items: list[FactCheckItem],
) -> list[FactCheckCorrection]:
    """
    Lance une conversation Claude pour un groupe d'items.

    Boucle multi-tours : Claude peut enchaîner plusieurs web_search avant
    d'appeler soumettre_anomalies_H. On s'arrête quand :
      - stop_reason == "tool_use" et l'outil est soumettre_anomalies_H
      - stop_reason == "end_turn"
      - Nombre de tours >= MAX_TURNS (protection contre boucle infinie)
    """
    user_message = _format_items_for_prompt(items)
    messages: list[dict] = [{"role": "user", "content": user_message}]

    page_hints: dict[str, int] = {item.original_text: item.page_num for item in items}

    for turn in range(MAX_TURNS):
        response = await client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            tools=[WEB_SEARCH_TOOL, REPORTER_TOOL],  # type: ignore[list-item]
            messages=messages,
        )

        # Ajoute la réponse de l'assistant à l'historique
        messages.append({"role": "assistant", "content": response.content})

        # Cherche les tool_use dans la réponse
        tool_uses = [b for b in response.content if b.type == "tool_use"]

        # Pas d'outil appelé → conversation terminée naturellement
        if not tool_uses:
            logger.debug("Fact-checker : fin naturelle au tour %d", turn + 1)
            return []

        tool_results: list[dict] = []
        reporter_result: list[FactCheckCorrection] | None = None

        for tool_use in tool_uses:
            if tool_use.name == "soumettre_anomalies_H":
                # Outil de rapport atteint — on extrait les anomalies et on s'arrête
                anomalies_raw: list[dict] = tool_use.input.get("anomalies", [])
                reporter_result = _parse_anomalies(anomalies_raw, page_hints, items)
                # Fournir un tool_result pour clore la conversation proprement
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": "Anomalies enregistrées.",
                })
            else:
                # web_search ou autre outil géré par Anthropic côté serveur —
                # on renvoie un tool_result vide pour poursuivre la conversation.
                # (En réalité le serveur Anthropic remplit le contenu lui-même ;
                #  on ajoute quand même un placeholder pour les éventuels outils
                #  custom qui seraient ajoutés plus tard.)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": "",
                })

        # Si le reporter a été appelé, on retourne directement ses résultats
        if reporter_result is not None:
            return reporter_result

        # Sinon on poursuit la conversation avec les tool_results
        messages.append({"role": "user", "content": tool_results})

    logger.warning("Fact-checker : MAX_TURNS atteint sans appel au reporter (%d items)", len(items))
    return []


def _parse_anomalies(
    raw: list[dict],
    page_hints: dict[str, int],
    items: list[FactCheckItem],
) -> list[FactCheckCorrection]:
    """
    Convertit les anomalies brutes retournées par Claude en FactCheckCorrection.

    Toutes les corrections H sont systématiquement forcées à "À vérifier" —
    la catégorie H ne génère jamais de Certain/Probable : c'est à l'éditeur humain
    de valider en dernier ressort. Cela élimine les faux positifs à haute confiance.
    """
    corrections: list[FactCheckCorrection] = []
    # Index rapide item_type par original_text
    type_index: dict[str, str] = {i.original_text: i.item_type for i in items}

    for a in raw:
        original = (a.get("original_text") or "").strip()

        if not original:
            continue

        # Filtre basique : original doit être différent de la correction
        corrected = (a.get("corrected_text") or "").strip() or None
        if corrected and original.lower() == corrected.lower():
            logger.debug("Fact-checker : ignoré (original=correction) : %s", original[:60])
            continue

        page_num = a.get("page_hint") if a.get("page_hint") is not None else page_hints.get(original, 0)
        item_type = type_index.get(original, "proper_noun")

        type_descriptions = {
            "date": "Vérification suggérée : date historique",
            "proper_noun": "Vérification suggérée : orthographe du nom propre",
            "title": "Vérification suggérée : titre d'œuvre",
        }
        description = type_descriptions.get(item_type, "Vérification suggérée")

        # RÈGLE ABSOLUE : toute correction H est "À vérifier" — jamais Certain/Probable
        corrections.append(FactCheckCorrection(
            page_num=int(page_num),
            category="H",
            original_text=original,
            corrected_text=corrected,
            description=description,
            explanation=(a.get("explanation") or "")[:1000],
            source=(a.get("source") or "")[:300],
            confidence="À vérifier",  # Toujours forcé — l'éditeur valide
        ))

    return corrections


# ── Point d'entrée public ──────────────────────────────────────────────────────


async def check_facts(
    items: list[FactCheckItem],
    excluded_names: set[str] | None = None,
    progress_callback=None,
) -> list[FactCheckCorrection]:
    """
    Vérifie les faits via Claude + web_search natif Anthropic.

    Paramètres
    ----------
    items :
        Liste d'éléments suspects fournis par la passe Claude précédente.
    excluded_names :
        Noms propres attestés ≥2 fois dans le document. Ces noms ne sont jamais
        envoyés à Claude car le document fait autorité sur ses propres acteurs.
    progress_callback :
        Coroutine optionnelle ``async (group_idx, total_groups) -> None``
        appelée à chaque groupe traité.

    Retourne
    --------
    Liste de FactCheckCorrection, uniquement pour les anomalies Certain ou Probable.
    """
    if not items:
        return []

    # Filtre 1 : validité minimale
    valid = [i for i in items if _is_valid(i.original_text)]

    # Filtre 2 : noms propres attestés dans le document → exclus
    if excluded_names:
        before = len(valid)
        valid = [
            i for i in valid
            if not (
                i.item_type == "proper_noun"
                and i.original_text in excluded_names
            )
        ]
        excluded_count = before - len(valid)
        if excluded_count > 0:
            logger.info(
                "Fact-checker : %d nom(s) propre(s) attesté(s) exclus de la vérification",
                excluded_count,
            )

    logger.info(
        "Fact-checker : %d éléments à vérifier (%d filtrés)",
        len(valid), len(items) - len(valid),
    )

    if not valid:
        return []

    # Découpage en groupes de GROUP_SIZE
    groups: list[list[FactCheckItem]] = [
        valid[i : i + GROUP_SIZE] for i in range(0, len(valid), GROUP_SIZE)
    ]
    total_groups = len(groups)
    logger.info("Fact-checker : %d groupe(s) de %d items", total_groups, GROUP_SIZE)

    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def check_group_with_sem(group: list[FactCheckItem], idx: int) -> list[FactCheckCorrection]:
        async with sem:
            if progress_callback:
                await progress_callback(idx, total_groups)
            try:
                return await _check_group(client, group)
            except Exception as exc:
                logger.warning(
                    "Fact-checker : erreur groupe %d/%d — %s",
                    idx + 1, total_groups, exc,
                )
                return []

    tasks = [check_group_with_sem(group, i) for i, group in enumerate(groups)]
    results = await asyncio.gather(*tasks)

    corrections: list[FactCheckCorrection] = [c for group_res in results for c in group_res]

    logger.info(
        "Fact-checker : %d anomalie(s) sur %d élément(s) vérifiés",
        len(corrections), len(valid),
    )
    return corrections
