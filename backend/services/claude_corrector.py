"""
Claude API — correcteur éditorial principal.

ARCHITECTURE TRIPLE PASSE (temperature=0 partout) :
  Passe 1a : A/B/C/D par lots de 3 pages — parallèle (max 5 simultanés)
  Passe 1b : Relecture rapide A/B/C — skippée si < 3 erreurs A/B/C
  Passe 2  : E/F/G — document entier, vision globale indispensable
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import anthropic

from backend.config import settings

logger = logging.getLogger(__name__)

# Fix B — adaptive batch size (overridden at runtime in correct_document)
PAGES_PER_BATCH = 3             # Passe 1a — baseline for short docs (≤30 pages)
MAX_FULL_TEXT_CHARS = 160_000   # Passe 2 — cap document entier (~40K tokens, couvre 200+ pages)
RELECTURE_MAX_CHARS = 80_000    # Passe 1b — cap document entier
MAX_CONCURRENT_BATCHES = 4      # Passe 1a — baseline (réduit à 2 pour docs >80 pages dans correct_document)
_RATE_LIMIT_MAX_RETRIES = 5     # Retries rate-limit (était 2 → trop agressif sur gros docs)
_RATE_LIMIT_BACKOFF = [20, 45, 90, 150, 240]  # secondes d'attente progressive

# Fix A — Pass 2 sliding-window for long documents
# full_text <= PASS2_SINGLE_CALL_CHARS  →  single Pass 2 call (unchanged behaviour)
# full_text >  PASS2_SINGLE_CALL_CHARS  →  chunked calls with overlap, capped at PASS2_MAX_CHUNKS
PASS2_SINGLE_CALL_CHARS = 90_000   # threshold for single-call mode
PASS2_CHUNK_SIZE        = 80_000   # chars per chunk (~20K tokens)
PASS2_CHUNK_OVERLAP     = 10_000   # overlap between consecutive chunks
PASS2_MAX_CHUNKS        = 6        # cap: 6 × 80K = ~480K chars ≈ 120 pages

# ── Contexte par type de document ─────────────────────────────────────────────

DOC_TYPE_CONTEXT: dict[str, str] = {
    "roman": (
        "Tu analyses un roman ou texte littéraire. "
        "Respecte les particularités stylistiques de l'auteur (phrases longues, "
        "syntaxe volontaire, registre particulier). "
        "Les noms de personnages de fiction ne sont JAMAIS des fautes d'orthographe."
    ),
    "bd_comics": (
        "Tu analyses une bande dessinée, un comics ou un manga. Règles spécifiques :\n"
        "1. Les onomatopées (POW !, CRAC !, BOUM !, etc.) sont correctes — ne pas les signaler.\n"
        "2. Le texte dans les bulles peut être intentionnellement fragmenté, exclamatif ou sans verbe.\n"
        "3. Les noms de personnages de fiction ne sont JAMAIS des fautes d'orthographe.\n"
        "4. Le registre familier, argotique ou les interjections sont des choix stylistiques.\n"
        "5. Les majuscules dans les bulles de BD sont conventionnelles — ne pas les signaler en typographie."
    ),
    "jeunesse": (
        "Tu analyses un livre jeunesse ou un album illustré. Règles spécifiques :\n"
        "1. La syntaxe simplifiée et les phrases courtes sont intentionnelles.\n"
        "2. Les répétitions pédagogiques (refrains, formules récurrentes) sont des choix d'auteur.\n"
        "3. Les noms de personnages inventés ne sont JAMAIS des fautes.\n"
        "4. Le registre oral et les formulations enfantines sont des choix délibérés.\n"
        "5. Concentre-toi sur les fautes réelles (orthographe, accord) et non sur le style simplifié."
    ),
    "poesie_theatre": (
        "Tu analyses un texte poétique ou théâtral. Règles impératives :\n"
        "1. La licence poétique (inversions, ellipses, enjambements, vers sans verbe) est correcte.\n"
        "2. Ne jamais signaler une coupe de vers ou un enjambement comme fragment de phrase.\n"
        "3. Les didascalies (indications scéniques) peuvent être en italiques et entre parenthèses — correct.\n"
        "4. Les noms de personnages en majuscules dans un texte de théâtre sont conventionnels.\n"
        "5. La ponctuation poétique non standard est souvent un choix d'auteur — utiliser 'À vérifier'."
    ),
    "documentaire": (
        "Tu analyses un ouvrage documentaire ou scientifique. Règles spécifiques :\n"
        "1. La terminologie scientifique spécialisée est prioritaire — vérifier l'exactitude sémantique.\n"
        "2. Les références bibliographiques (auteur, date, titre, éditeur) sont à vérifier en fact-check.\n"
        "3. Les unités de mesure, formules et nomenclatures scientifiques sont des données factuelles.\n"
        "4. Les notes de bas de page et renvois (G) sont particulièrement importants à contrôler.\n"
        "5. Les latinismes scientifiques (ibid., op. cit., etc.) sont corrects en italiques."
    ),
    "tourisme": (
        "Tu analyses un guide touristique ou un ouvrage de voyage. Règles spécifiques :\n"
        "1. Les noms de lieux, hôtels, restaurants, monuments sont à vérifier en fact-check.\n"
        "2. Les noms étrangers (villes, régions, sites) doivent conserver leur orthographe d'origine.\n"
        "3. Les horaires, distances, prix sont des données factuelles — ne pas modifier le format.\n"
        "4. Les mots étrangers dans leur contexte culturel original (piazza, rambla, etc.) sont corrects.\n"
        "5. L'uniformisation F est importante : même graphie pour les noms propres et les termes géographiques."
    ),
    "cuisine": (
        "Tu analyses un livre de cuisine ou un ouvrage gastronomique. Règles spécifiques :\n"
        "1. Les noms de techniques culinaires (julienne, brunoise, blanchir, etc.) sont des termes spécialisés corrects.\n"
        "2. Les unités de mesure (g, kg, ml, cl, °C, min) sont des abréviations standardisées — ne pas corriger.\n"
        "3. Les noms d'ingrédients étrangers (mozzarella, tahini, wakamé, etc.) sont corrects.\n"
        "4. L'uniformisation F est cruciale : vérifier la cohérence des quantités, températures et durées.\n"
        "5. Les listes d'ingrédients sans verbe sont des formats intentionnels — ne pas signaler comme fragments."
    ),
    "sport": (
        "Tu analyses un ouvrage sportif ou de bien-être. Règles spécifiques :\n"
        "1. La terminologie sportive spécialisée (termes techniques, règles de jeu) est correcte.\n"
        "2. Les noms d'athlètes, d'équipes, de compétitions et de fédérations sont à vérifier en fact-check.\n"
        "3. Les statistiques, scores, classements et records sont des données factuelles sensibles.\n"
        "4. Les anglicismes sportifs établis (dribble, match, sprint, penalty) sont acceptés.\n"
        "5. Les unités de performance (km/h, secondes, mètres) ne sont pas à corriger."
    ),
    "manuel_scolaire": (
        "Tu analyses un MANUEL SCOLAIRE. Règles impératives :\n"
        "1. IGNORER complètement les zones d'exercices : lignes de pointillés (……), "
        "tirets répétés (---), espaces réservés (___), lettres isolées ou espacées servant "
        "de cases à compléter (ex : « D  E  C », « X  D E  S », « _ _ _ »), "
        "cases à cocher, tableaux vides à remplir.\n"
        "2. IGNORER les textes intentionnellement fautifs dans les exercices "
        "(textes que l'élève doit corriger).\n"
        "3. CORRIGER normalement les consignes d'exercices et le texte courant du manuel.\n"
        "4. Les numérotations d'exercices (Ex. 1, Ex. 2, A., B.…) ne sont pas des fautes.\n"
        "5. Si une citation entre guillemets est dans une consigne ou un exemple pédagogique, "
        "ce n'est PAS un titre d'œuvre — ne pas l'inclure dans fact_check_items."
    ),
    "parascolaire": (
        "Tu analyses un ouvrage parascolaire ou d'aide scolaire. Règles spécifiques :\n"
        "1. IGNORER les zones d'exercices : espaces à remplir, pointillés, cases vides.\n"
        "2. IGNORER les exemples volontairement fautifs fournis pour que l'élève les corrige.\n"
        "3. CORRIGER normalement les consignes, explications et contenus pédagogiques.\n"
        "4. Les numérotations et repères (Exercice 1, Étape A, etc.) sont corrects.\n"
        "5. Le niveau de langue peut être simplifié intentionnellement — respecter le registre."
    ),
    "essai": (
        "Tu analyses un essai académique, scientifique ou un rapport. "
        "Sois attentif à la rigueur terminologique et aux références bibliographiques. "
        "Les citations entre guillemets sont des extraits à reproduire fidèlement."
    ),
    "beaux_arts": (
        "Tu analyses un livre d'art, un catalogue d'exposition, un ouvrage d'architecture, de design ou de photographie. Règles spécifiques :\n"
        "1. Les noms d'artistes, d'architectes, de mouvements artistiques et d'œuvres sont à vérifier en fact-check (cat. H).\n"
        "2. Les dates de création, d'exposition et de naissance/mort sont des données factuelles sensibles.\n"
        "3. Les noms de galeries, musées, fondations, maisons d'édition d'art sont à vérifier.\n"
        "4. Les termes techniques propres à chaque discipline (droit fil, linteau, sérigraphie, etc.) sont corrects.\n"
        "5. Les titres d'œuvres entre guillemets ou en italique sont à reproduire exactement — ne pas 'corriger' leur orthographe.\n"
        "6. Les textes de légendes d'images peuvent être elliptiques (sans verbe) — format intentionnel."
    ),
    "autre": (
        "Tu analyses un document général. Applique les règles éditoriales standard."
    ),
    "magazine": (
        "Tu analyses un magazine ou une revue grand public. Règles spécifiques :\n"
        "1. Les titres d'articles en capitales ou en gras sont des choix éditoriaux — ne pas signaler la casse.\n"
        "2. Les accroches, chapeaux et intertitres peuvent être elliptiques (sans verbe) — format voulu.\n"
        "3. Les légendes de photos sont souvent courtes et nominales — format intentionnel.\n"
        "4. Les encadrés, chiffres clés et citations en exergue ont leur propre typographie — respecter les conventions maison.\n"
        "5. Les anglicismes établis dans la presse (best of, live, breaking news, etc.) sont acceptés.\n"
        "6. Les noms de rubriques récurrentes (En bref, Tendances, À la une…) ne sont pas des fautes."
    ),
    "revue_presse": (
        "Tu analyses une revue de presse, un journal ou une publication périodique. Règles spécifiques :\n"
        "1. Les titres d'articles et manchettes peuvent utiliser des majuscules stylistiques — ne pas signaler.\n"
        "2. Les accroches et sous-titres peuvent être elliptiques — format journalistique intentionnel.\n"
        "3. Les dépêches peuvent inclure des abréviations de presse (AFP, AP, etc.) — corrects.\n"
        "4. Les noms de sources (ministères, agences, titres de presse) sont à vérifier en fact-check (H).\n"
        "5. Les dates et chiffres liés à l'actualité sont des données factuelles sensibles — utiliser 'À vérifier'.\n"
        "6. Les citations de personnalités publiques entre guillemets sont à reproduire exactement."
    ),
}

# ── Prompt Passe 1a (A/B/C/D) ─────────────────────────────────────────────────

_SYSTEM_PASS1_BASE = """Tu es un correcteur éditorial professionnel, exhaustif et rigoureux.

PASSE 1 — Analyse paragraphe par paragraphe : catégories A, B, C, D.

**RÈGLE ANTI-HALLUCINATION :** Ne signale QUE ce qui est textuellement présent dans l'extrait fourni. Aucune inférence sur ce qui pourrait exister ailleurs.

**CORRECTION OBLIGATOIRE :** "corrected" = forme corrigée concrète. Jamais une phrase explicative, jamais vide. Si incertain, propose ta meilleure hypothèse — le niveau de confiance porte l'incertitude.

---

**MÉTHODE :** Pour chaque paragraphe, vérifier dans cet ordre avant de passer au suivant :
□ A — Orthographe : mots mal orthographiés, accents, homophones
□ B — Grammaire : accords sujet/verbe, adjectif/nom, participes passés, conjugaison
□ C — Typographie : apostrophes ('), guillemets (« »), points de suspension (…), tirets (– —), ligatures (œ, æ)
□ D — Syntaxe : phrase grammaticalement impossible ou induisant une confusion de sens (PAS les tournures stylistiques)

---

**A — ORTHOGRAPHE** (StrikeOut rouge)
- Mot mal orthographié, accent manquant ou incorrect (é/è/ê, à/a, où/ou, ô/o)
- Homophone mal utilisé (a/à, ou/où, ces/ses/c'est, leur/leurs, on/ont, son/sont…)
- Tiret composé manquant (c'est-à-dire, vis-à-vis…), pluriel ou féminin erroné
⚠ JAMAIS : noms de personnages de fiction, noms d'auteurs, termes étrangers identifiés.

**B — GRAMMAIRE** (StrikeOut orange)
- Accord sujet/verbe incorrect (même si sujet éloigné)
- Accord adjectif/nom, conjugaison erronée, mauvais temps verbal
- Accord du participe passé (avoir : COD avant ; pronominaux), confusion er/é
⚠ JAMAIS : constructions stylistiques intentionnelles.
⚠ TITRE D'ŒUVRE COMME SUJET : si le sujet grammatical est un titre entre guillemets (ex : « Les Contemplations », « Les Misérables »), NE JAMAIS utiliser "Certain" ou "Probable" pour l'accord du verbe — le titre peut être traité comme nom propre singulier. Utiliser "À vérifier" uniquement si l'erreur est vraiment manifeste dans le contexte.

**C — TYPOGRAPHIE** (Highlight violet)
- Apostrophe droite (') → typographique ('), guillemets droits (" ") → français (« »)
- Guillemets anglais ("…" ou "…") → français (« … »)
- Trois points (...) → ellipse (…), trait d'union (-) → tiret demi-cadratin (–) ou cadratin (—) si requis
- Ligature absente : « oe » → « œ », « ae » → « æ »
⚠ GUILLEMETS FRANÇAIS « » DÉJÀ PRÉSENTS = CORRECTS — NE JAMAIS LES SIGNALER, même si l'espace intérieure semble inhabituelle.
⚠ Signaler UNIQUEMENT : guillemets droits ("…"), guillemets anglais ("…"/"…"), apostrophes droites (') utilisées à la place de guillemets.
⚠ JAMAIS : tirets décoratifs de sommaires, espaces d'exercices.
⚠ SIÈCLES EN CHIFFRES ROMAINS : « XVIIe », « XVIIIe », « XIXe », « XXe », « XXIe »… — le "e" est un exposant aplati lors de l'extraction PDF. Ces formes sont CORRECTES, ne jamais les signaler en C ou en A.
⚠ NOMBRES ORDINAUX : « 1er », « 2e », « 3e », « 17e », « 18e », « 1re »… — l'exposant typographique (ᵉ, ᵉʳ) est aplati en lettre ordinaire lors de l'extraction PDF. Ces formes sont CORRECTES telles quelles, ne JAMAIS les signaler ou proposer « 17ᵉ » à la place de « 17e ».
⚠ ITALIQUE — NE JAMAIS signaler qu'un texte devrait être en italique ou ne l'est pas. Tu ne vois pas la mise en forme réelle du PDF, donc il t'est impossible de vérifier la présence ou l'absence d'italique dans le rendu final.

**D — SYNTAXE & STYLE** (Highlight bleu)
Signaler UNIQUEMENT si la phrase est grammaticalement impossible ou induit une confusion de sens.
PAS les tournures stylistiques, ellipses narratives ou choix d'auteur.
- Phrase sans sujet ni verbe principal (fragment non délibéré)
- Anacoluthe (rupture grammaticale involontaire)
- Redondance lexicale avérée (au jour d'aujourd'hui, prévoir à l'avance…)

---

**CONFIANCE (obligatoire) :**
- "Certain"    : règle fixe, sans exception possible (accord sujet/verbe évident, homophone clair).
- "Probable"   : erreur très probable, contexte stylistique à écarter manuellement.
- "À vérifier" : usage ambigu, convention variable, particularité possible.
NE PAS utiliser "À vérifier" pour les erreurs évidentes — préfère "Certain".

---

**IMPÉRATIF — ZÉRO FAUX POSITIF (catégories A, B, C) :**
- En cas de doute sur A, B ou C : utiliser "À vérifier" — JAMAIS forcer "Certain" ou "Probable".
- Si un texte est susceptible d'être correct dans son contexte (style d'auteur, titre d'œuvre, usage régional, registre historique), NE PAS le signaler ou utiliser "À vérifier".
- Un silence sur un cas ambigu est préférable à un faux positif.
- Pour les guillemets : ne signaler QUE ce qui est clairement fautif (guillemets droits ASCII " ", guillemets anglais " "). NE JAMAIS signaler des guillemets « » ou des apostrophes ' déjà typographiques.
- **RÈGLE ABSOLUE : si tu n'es pas certain à 100% qu'il y a une erreur, ne pas signaler. Un faux positif est pire qu'une correction manquée.**

---

**ÉLÉMENTS À IGNORER ABSOLUMENT :**
⚠ **EXERCICES PÉDAGOGIQUES :** Si tu identifies du contenu qui est manifestement un exercice interactif (mots à relier avec flèches ou tirets, QCM, texte à trous avec blancs/pointillés, mots mêlés, anagrammes, listes de mots à apparier), IGNORE entièrement ce contenu. Ne propose aucune correction sur les éléments d'un exercice, même si l'ordre ou la disposition semble incohérent — c'est voulu.
⚠ **CHIFFRES DE RENVOI :** Les chiffres en exposant (¹²³⁴⁵) collés à des mots dans le texte sont des appels de notes de bas de page. Ne les signale JAMAIS comme des caractères erronés ou des fautes.
⚠ **CHIFFRES ORDINAIRES COLLÉS À UN MOT OU À UN NOMBRE :** Lors de l'extraction PDF, les exposants de notes (¹, ², ³…) perdent souvent leur mise en forme et apparaissent comme des chiffres ordinaires collés (ex : « gauches1 », « auteur2 », « 681 » au lieu de « 68¹ »). Si la seule différence entre un nombre et ce qui précède est 1 ou 2 chiffres en fin, ou si un chiffre est collé à un mot courant, NE PAS le signaler — c'est un appel de note non encodé, pas une faute.
⚠ **GUILLEMETS « » DÉJÀ PRÉSENTS :** Si le texte extrait contient les caractères « ou », ces guillemets sont corrects en typographie française. Ne les signale en aucun cas, même si l'espace intérieure semble inhabituelle.
⚠ **COUPURES SYLLABIQUES DE FIN DE LIGNE :** Dans un PDF mis en page, les mots coupés en fin de ligne apparaissent dans le texte extrait sous la forme « piteu-sement », « contem-porains », « socio-logie » (tiret interne). Ce tiret est un artefact de mise en page — il N'EXISTE PAS dans le texte de l'auteur.
  - Si tu signales une telle coupure en catégorie C, la forme corrigée DOIT être exactement les deux parties réunies sans tiret : « piteu-sement » → « piteusement ». JAMAIS un mot différent (ex : proposer « pieusement » pour « piteu-sement » serait une erreur grave).
  - En cas de doute sur le mot réel (le mot soudé n'est pas évident), NE PAS signaler.

⚠ **ARTEFACTS D'EXTRACTION PDF (FILIGRANES) :** Certains PDF contiennent du texte décoratif imprimé en très grande police (noms d'artistes, titres en fond de page) qui apparaît fragmenté lors de l'extraction. Ces artefacts ne sont JAMAIS des fautes éditoriales. Ignorer absolument :
  - Fragments de 1 à 5 lettres en minuscules isolés qui ne forment pas un mot français ('lio', 'ranz', 'ager', 'ulio') — début ou fin de nom propre rogné
  - Mots avec une majuscule interne parasite ('MIngres', 'KINé', 'savIez', 'eanacques') — casse brisée par l'extraction
  - Séquences tronquées comme 'ranz Ma', 'e Corbus', 'agerfeld', 'eanacques' — fragments de grands caractères décoratifs
  Ne jamais signaler ces formes, même en catégorie A « À vérifier ».

---

**ÉLÉMENTS À VÉRIFIER (fact_check_items) :**
Inclure UNIQUEMENT :
- Noms propres réels multi-mots (personnes historiques, lieux réels) dont tu doutes de l'orthographe
- Dates historiques précises liées à un événement
- Titres d'œuvres réelles (≥ 2 mots) publiées, potentiellement mal reproduits

Ne PAS inclure :
- Noms de personnages de fiction, textes d'exercices scolaires
- Noms propres répétés de façon cohérente dans le document (ils sont attestés)
- Légendes bibliographiques d'images au format « Nom, Titre, technique, date, lieu »

---

---

**RÈGLE DE PRÉCISION OBLIGATOIRE :**
Dans le champ "original" : n'extraire QUE le mot fautif ou l'expression courte concernée. Pas la phrase entière. Exemples :
  ✓ "ou" (homophone fautif), "appele" (faute d'orthographe), "...", '"texte"'
  ✗ "Julien se rendit au village ou il passa la nuit" (trop long — ne prendre que "ou")

**ITALIQUE — marquage de mise en forme uniquement :**
⚠ UNIQUEMENT si le texte extrait dans "original" est DÉJÀ en italique dans le PDF source (titre d'œuvre en italique, mot étranger en italique) : l'entourer avec *...* dans "original" ET "corrected" pour signaler la mise en forme.
  Exemple : original = "*Le Rouge et le Noir*" → corrected = "*Le Rouge et le Noir*"
⚠ NE JAMAIS générer une correction pour signaler qu'un texte devrait être mis en italique ou ne l'est pas — ce contrôle n'est pas de ton ressort.
⚠ NE PAS ajouter *...* si tu n'es pas certain que le texte est réellement en italique dans le PDF.

Appeler TOUJOURS l'outil soumettre_corrections_pass1, même si les listes sont vides."""

# ── Prompt Passe 1b — Relecture ciblée ───────────────────────────────────────

_SYSTEM_RELECTURE = """Tu es un relecteur éditorial de précision. Ta mission est de COMPLÉTER une correction déjà effectuée.

Un premier correcteur a analysé ce document. Tu dois trouver UNIQUEMENT ce qui a été manqué.

CONCENTRE-TOI sur les erreurs les plus souvent manquées :

**A — Orthographe**
- Homophones (a/à, ou/où, ces/ses/c'est, leur/leurs, on/ont, son/sont, peu/peut…)
- Accents manquants ou incorrects sur mots courants
- Consonnes doubles oubliées (appeler → appelle, jeter → jette…)

**B — Grammaire**
- Accord du participe passé avec avoir quand le COD précède
- Accord du participe passé des verbes pronominaux
- Accord de l'adjectif attribut avec un sujet éloigné ou complexe

**C — Typographie**
- Apostrophe droite (') non corrigée → (')
- Guillemet droit ("…") non corrigé → (« … »)
- Trois points (...) non corrigés → (…)

RÈGLES :
- Ne PAS signaler les erreurs déjà listées dans "DÉJÀ TROUVÉES".
- "corrected" = forme corrigée concrète TOUJOURS.
- Catégories autorisées : A, B, C uniquement.

Appelle l'outil soumettre_corrections_pass1 avec les erreurs NOUVELLES uniquement."""

# ── Prompt Passe 2 (E/F/G) ────────────────────────────────────────────────────

_SYSTEM_PASS2_BASE = """Tu es un correcteur éditorial spécialisé dans l'analyse globale de documents.

PASSE 2 — Analyse du document ENTIER : catégories E, F, G.
Tu reçois l'intégralité du texte avec numéros de page.
Ta mission : détecter les incohérences QUI NE PEUVENT PAS être vues page par page.

---

**RÈGLE ABSOLUE SUR LES CORRECTIONS :**
"corrected" = forme corrigée concrète OBLIGATOIRE.
Pour F : indique la forme RETENUE (ex : "clé" si c'est la forme dominante).
Jamais "voir commentaire".

---

**E — SÉMANTIQUE & COHÉRENCE** (Highlight vert)
- Mot employé à contre-sens (propose le mot juste)
- Terme technique employé hors de son domaine sémantique propre : vérifier que chaque terme spécialisé est utilisé dans son acception exacte pour le domaine traité (ex : "migrer" est un terme biologique/informatique — pour l'âme dans les doctrines bouddhistes ou hindouistes, le terme correct est "se réincarner" ou "transmigrer" ; idem pour les termes historiques, juridiques, scientifiques)
- Définition inexacte ou incomplète d'un terme littéraire ou historique
- Contradiction entre deux passages du document (cite les deux pages dans l'explanation)
- Anachronisme dans un commentaire ou une définition
- **Titre ou en-tête de section incohérent avec le contenu du passage qui suit immédiatement** : si un titre annonce un sujet (ex : "Paysages romantiques") mais le texte traite d'autre chose (ex : portraits ou nature morte), signaler l'incohérence en indiquant la page du titre et la nature de l'écart. Utiliser "original" = le titre exact, "corrected" = le sujet réel du contenu (ex : "Scènes de genre").

**F — UNIFORMISATION** (Squiggly cyan)
Compare SYSTÉMATIQUEMENT les occurrences à travers tout le document :
- Même mot orthographié différemment selon les pages (ex : "clé" p.3 vs "clef" p.18)
- **Majuscule incohérente sur un même terme selon les pages** — notamment :
  · Titres et fonctions (ex : "l'Empereur" p.5 vs "l'empereur" p.12 ; "le Roi" vs "le roi")
  · Désignations récurrentes importantes (ex : "la République" vs "la république")
  · Noms propres abrégés ou génériques utilisés comme noms propres dans le contexte
  Si un terme désigne un individu ou une instance spécifique dans le document, la majuscule doit être constante.
- Format de date incohérent pour le même type d'information
- Ponctuation finale incohérente dans des listes parallèles
- Ligature présente dans certains passages et absente dans d'autres
Pour chaque F : indique les deux formes et les pages où elles apparaissent.

**G — RENVOIS & PREMIÈRES OCCURRENCES** (Squiggly rose)
- Note de bas de page absente à la PREMIÈRE occurrence d'un terme défini plus loin
- Renvoi de page ou de ligne incorrect
- Placeholder non remplacé : XX, XXX, Xxxxx, ILLU, TBD, À compléter
- Référence à une note qui pointe vers la mauvaise note

⚠ F — NE JAMAIS signaler : les interlignes, l'espacement entre paragraphes, les sauts de ligne dus à la mise en page. ÉditorIA traite le TEXTE, pas la mise en page. Une ligne de blanc entre deux paragraphes n'est pas une erreur F.

⚠ PHRASE TRONQUÉE — NE SIGNALER QUE les troncatures réelles et visibles dans le texte lui-même : mot coupé net, phrase ouverte sans fin (ex : guillemet ouvert non fermé, parenthèse ouverte sans fermeture). NE JAMAIS signaler qu'une phrase se terminant normalement en fin de segment est « tronquée » : dans une mise en page normale, les phrases et paragraphes enjambent les pages — la suite est simplement dans la page suivante. Un signalement "préventif" ou fondé sur l'absence de suite visible dans le segment courant EST INTERDIT.

⚠ PLACEHOLDER — NE signaler QUE si le placeholder (XX, Xxxxx, ILLU, TBD…) est TEXTUELLEMENT PRÉSENT dans l'extrait fourni. Ne jamais signaler l'absence de placeholder comme une erreur, ne jamais émettre un signalement préventif.

---

**NIVEAU DE CONFIANCE (obligatoire) :**
- "Certain"    : incohérence clairement attestée dans le texte.
- "Probable"   : incohérence probable, contexte stylistique possible.
- "À vérifier" : doute raisonnable, à confirmer.

---

Appeler TOUJOURS l'outil soumettre_corrections_pass2, même si la liste est vide."""

# ── Tool schemas ───────────────────────────────────────────────────────────────

_CORR_ITEM_PROPS = {
    "original":    {"type": "string", "description": "MOT ou EXPRESSION EXACTE à corriger (2-80 caractères). Extraire le MINIMUM nécessaire : uniquement le mot fautif ou l'expression courte, pas la phrase entière. Pour les textes en italique (titres d'œuvres, mots étrangers), entourer avec *...* ex: *Le Misanthrope*."},
    "corrected":   {"type": "string", "description": "Forme corrigée concrète OBLIGATOIRE. Pour les textes en italique, conserver *...* ex: *Le Misanthrope*."},
    "category":    {"type": "string"},
    "page_hint":   {"type": "integer", "description": "Numéro de page 1-indexé."},
    "description": {"type": "string", "description": "Description courte en français (max 8 mots)."},
    "explanation": {"type": "string", "description": "Explication détaillée en français."},
    "source":      {"type": "string", "description": "Source en français."},
    "confidence":  {"type": "string", "enum": ["Certain", "Probable", "À vérifier"]},
}
_CORR_REQUIRED = ["original", "corrected", "category", "page_hint",
                   "description", "explanation", "source", "confidence"]

TOOLS_PASS1: list[dict[str, Any]] = [
    {
        "name": "soumettre_corrections_pass1",
        "description": "Soumet les corrections A/B/C/D et les éléments à vérifier en ligne.",
        "input_schema": {
            "type": "object",
            "properties": {
                "corrections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            **_CORR_ITEM_PROPS,
                            "category": {"type": "string", "enum": ["A", "B", "C", "D"]},
                        },
                        "required": _CORR_REQUIRED,
                    },
                },
                "fact_check_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text":      {"type": "string"},
                            "context":   {"type": "string"},
                            "item_type": {"type": "string", "enum": ["date", "proper_noun", "title"]},
                            "page_hint": {"type": "integer"},
                        },
                        "required": ["text", "context", "item_type", "page_hint"],
                    },
                },
            },
            "required": ["corrections", "fact_check_items"],
        },
    }
]

TOOLS_PASS2: list[dict[str, Any]] = [
    {
        "name": "soumettre_corrections_pass2",
        "description": "Soumet les corrections E/F/G après analyse globale du document.",
        "input_schema": {
            "type": "object",
            "properties": {
                "corrections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            **_CORR_ITEM_PROPS,
                            "category": {"type": "string", "enum": ["E", "F", "G"]},
                        },
                        "required": _CORR_REQUIRED,
                    },
                },
            },
            "required": ["corrections"],
        },
    }
]

# ── Config catégories ──────────────────────────────────────────────────────────

CATEGORY_CONFIG: dict[str, dict] = {
    "A": {"name": "A – Orthographe",    "color": (1.00, 0.65, 0.65), "annotation_type": "StrikeOut"},
    "B": {"name": "B – Grammaire",      "color": (1.00, 0.82, 0.55), "annotation_type": "StrikeOut"},
    "C": {"name": "C – Typographie",    "color": (0.78, 0.60, 1.00), "annotation_type": "Highlight"},
    "D": {"name": "D – Syntaxe",        "color": (0.55, 0.78, 1.00), "annotation_type": "Highlight"},
    "E": {"name": "E – Sémantique",     "color": (0.60, 0.90, 0.65), "annotation_type": "Highlight"},
    "F": {"name": "F – Uniformisation", "color": (0.55, 0.90, 0.95), "annotation_type": "Squiggly"},
    "G": {"name": "G – Renvois",        "color": (1.00, 0.72, 0.87), "annotation_type": "Squiggly"},
}

PASS1_CATS = {"A", "B", "C", "D"}
PASS2_CATS = {"E", "F", "G"}


@dataclass
class _ApiUsage:
    """Accumule les tokens consommés sur toutes les passes Claude."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, response: Any) -> None:
        try:
            u = response.usage
            self.input_tokens += getattr(u, 'input_tokens', 0) or 0
            self.output_tokens += getattr(u, 'output_tokens', 0) or 0
            self.cache_read_tokens += getattr(u, 'cache_read_input_tokens', 0) or 0
            self.cache_write_tokens += getattr(u, 'cache_creation_input_tokens', 0) or 0
        except Exception:
            pass

    def cost_usd(self) -> float:
        """Coût estimé — tarifs claude-sonnet-4-x."""
        return (
            self.input_tokens   * 3e-6 +
            self.output_tokens  * 15e-6 +
            self.cache_write_tokens * 3.75e-6 +
            self.cache_read_tokens  * 0.3e-6
        )


@dataclass
class ClaudeCorrection:
    page_num: int
    category: str
    original_text: str
    corrected_text: str | None
    description: str
    explanation: str
    source: str
    confidence: str


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_system(base: str, doc_type: str, metadata: dict | None = None) -> str:
    ctx = DOC_TYPE_CONTEXT.get(doc_type, DOC_TYPE_CONTEXT["autre"])
    result = f"{base}\n\n---\n\n**CONTEXTE DU DOCUMENT :**\n{ctx}"
    if metadata:
        parts = []
        if metadata.get("author"):
            parts.append(f"- Auteur : {metadata['author']} — NE JAMAIS signaler comme faute")
        if metadata.get("title"):
            parts.append(f"- Titre de l'œuvre : {metadata['title']} — NE JAMAIS signaler comme faute ou erreur grammaticale")
        if metadata.get("characters"):
            parts.append(f"- Noms de personnages/acteurs : {metadata['characters']} — NE JAMAIS signaler comme fautes")
        if metadata.get("citation_lang"):
            parts.append(f"- Langue des citations : {metadata['citation_lang']} — les passages dans cette langue sont CORRECTS")
        if metadata.get("house_rules"):
            parts.append(f"- Règles typographiques maison : {metadata['house_rules']} — appliquer rigoureusement et en priorité sur les règles générales")
        if parts:
            result += "\n\n---\n\n**MÉTADONNÉES (exclusions absolues) :**\n" + "\n".join(parts)
        # Patterns faux positifs appris
        fp_patterns = metadata.get("_fp_patterns", "")
        if fp_patterns:
            result += f"\n\n---\n\n{fp_patterns}"
    return result


def _strip_html(text: str) -> str:
    """Supprime les balises HTML que Claude peut insérer (ex: <sup>, <em>)."""
    return re.sub(r'<[^>]+>', '', text).strip()


def _parse_correction(rc: dict, valid_cats: set[str], default_page: int) -> ClaudeCorrection | None:
    # Guard: Claude peut parfois retourner une string au lieu d'un dict dans la liste
    if not isinstance(rc, dict):
        logger.warning(
            "_parse_correction: élément inattendu (type %s) ignoré: %r",
            type(rc).__name__,
            str(rc)[:80],
        )
        return None
    original = _strip_html((rc.get("original") or "").strip())
    # Validation longueur : 2 chars min (mots courts), 150 chars max (mot/expression seulement)
    stripped_len = len(original.replace("*", ""))
    if not original or stripped_len < 2 or len(original) > 150:
        return None
    cat = rc.get("category", "D")
    if cat not in valid_cats:
        return None
    page_num = max(0, int(rc.get("page_hint", default_page + 1)) - 1)
    corrected = _strip_html((rc.get("corrected") or "").strip())
    # Rejeter les non-corrections
    BAD = {"voir commentaire", "à vérifier", "voir note", "à confirmer", "—", "-", ""}
    if corrected.lower() in BAD:
        corrected = None
    # Rejeter les corrections identiques à l'original (insensible à la casse)
    if corrected and corrected.strip().lower() == original.strip().lower():
        return None
    # Trimmer au seul mot modifié quand original et corrected ont le même nombre de mots
    # Ex : "Seuls les apparences" → "Seuls" ; "Seules les apparences" → "Seules"
    if corrected and len(original.split()) > 1:
        orig_words = original.split()
        corr_words = corrected.split()
        if len(orig_words) == len(corr_words):
            diff = [i for i in range(len(orig_words))
                    if orig_words[i].lower() != corr_words[i].lower()]
            if len(diff) == 1:
                i = diff[0]
                original = orig_words[i]
                corrected = corr_words[i]
    confidence = rc.get("confidence", "Probable")
    if confidence not in ("Certain", "Probable", "À vérifier"):
        confidence = "Probable"
    return ClaudeCorrection(
        page_num=page_num,
        category=cat,
        original_text=original,
        corrected_text=corrected or None,
        description=(rc.get("description") or "")[:200],
        explanation=(rc.get("explanation") or "")[:1000],
        source=(rc.get("source") or "Connaissance d'entraînement (règle stable)")[:300],
        confidence=confidence,
    )


def _build_condensed_text(pages: list, max_chars: int) -> str:
    """
    Assemble le texte de toutes les pages en respectant max_chars.

    Stratégie :
    - Si le document entier tient, retourner tel quel.
    - Sinon, distribuer le budget proportionnellement par page en nombre de caractères
      réels (non linéaire : évite de tronquer brutalement les longues pages à un ratio
      identique et de perdre entièrement les petites pages).
    - Garantit qu'au moins les 200 premiers caractères de chaque page sont conservés
      pour préserver le contexte de numérotation et d'en-tête.
    """
    non_empty = [(p.page_num, p.text) for p in pages if p.text.strip()]
    if not non_empty:
        return ""

    parts: list[str] = []
    for page_num, text in non_empty:
        parts.append(f"\n--- PAGE {page_num + 1} ---\n{text}")
    full = "".join(parts)
    if len(full) <= max_chars:
        return full

    # Budget par page : proportionnel mais avec un minimum garanti de 200 chars
    min_per_page = 200
    total_text_chars = sum(len(t) for _, t in non_empty)
    available = max(0, max_chars - len(non_empty) * (min_per_page + 25))  # 25 = header overhead

    trimmed: list[str] = []
    for page_num, text in non_empty:
        page_ratio = len(text) / total_text_chars if total_text_chars > 0 else 0
        budget = min_per_page + int(available * page_ratio)
        kept = text[:budget]
        # Ne pas couper au milieu d'un mot
        if len(text) > budget and budget > 0 and not text[budget:budget + 1].isspace():
            last_space = kept.rfind(" ")
            if last_space > min_per_page:
                kept = kept[:last_space]
        trimmed.append(f"\n--- PAGE {page_num + 1} ---\n{kept}")
    return "".join(trimmed)


# ── Passe 1a — analyse par lots de 3 pages ────────────────────────────────────

def _build_pass1_message(
    batch: list[tuple[int, str]],
    proper_noun_variants: dict[str, list[str]],
) -> str:
    parts: list[str] = []
    if proper_noun_variants:
        parts.append("NOMS PROPRES ATTESTÉS DANS CE DOCUMENT (ne jamais signaler comme fautes) :\n")
        for name, variants in list(proper_noun_variants.items())[:30]:
            if len(variants) == 1:
                parts.append(f"  • {name}\n")
            else:
                parts.append(f"  • {name} — variantes : {', '.join(variants)} → à signaler en F\n")
        parts.append("\n")

    parts.append(
        "Analyse chaque paragraphe de chaque page en suivant la checklist A→B→C→D.\n"
        "'corrected' = forme corrigée concrète OBLIGATOIRE pour chaque correction.\n"
        "Indique le niveau de confiance (Certain / Probable / À vérifier).\n\n"
    )
    for page_num, text in batch:
        parts.append(f"--- PAGE {page_num + 1} ---\n{text}\n\n")
    parts.append("Appelle maintenant l'outil soumettre_corrections_pass1.")
    return "".join(parts)


async def _process_batch_pass1(
    client: anthropic.AsyncAnthropic,
    batch: list[tuple[int, str]],
    proper_noun_variants: dict[str, list[str]],
    doc_type: str,
    attempt: int = 0,
    metadata: dict | None = None,
    usage: _ApiUsage | None = None,
) -> tuple[list[ClaudeCorrection], list[dict]]:
    system = _build_system(_SYSTEM_PASS1_BASE, doc_type, metadata)
    user = _build_pass1_message(batch, proper_noun_variants)

    try:
        response = await client.messages.create(
            model=settings.CLAUDE_FAST_MODEL,
            max_tokens=8192,
            temperature=0,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS_PASS1,
            tool_choice={"type": "tool", "name": "soumettre_corrections_pass1"},
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.RateLimitError:
        if attempt < _RATE_LIMIT_MAX_RETRIES:
            wait = _RATE_LIMIT_BACKOFF[min(attempt, len(_RATE_LIMIT_BACKOFF) - 1)]
            logger.warning("Rate limit (tentative %d/%d) — attente %ds…", attempt + 1, _RATE_LIMIT_MAX_RETRIES, wait)
            await asyncio.sleep(wait)
            return await _process_batch_pass1(client, batch, proper_noun_variants, doc_type, attempt + 1, metadata, usage)
        logger.error("Rate limit persistant après %d tentatives — lot abandonné pp.%s",
                     _RATE_LIMIT_MAX_RETRIES, [p for p, _ in batch])
        return [], []
    except Exception as exc:
        logger.error("Claude Passe 1a erreur : %s", exc)
        return [], []

    if usage is not None:
        usage.add(response)

    # Redécoupage automatique si max_tokens
    if response.stop_reason == "max_tokens" and len(batch) > 1:
        logger.warning("max_tokens sur lot pp.%d-%d — redécoupage", batch[0][0] + 1, batch[-1][0] + 1)
        mid = len(batch) // 2
        c1, f1 = await _process_batch_pass1(client, batch[:mid], proper_noun_variants, doc_type, attempt, metadata, usage)
        c2, f2 = await _process_batch_pass1(client, batch[mid:], proper_noun_variants, doc_type, attempt, metadata, usage)
        return c1 + c2, f1 + f2

    corrections: list[ClaudeCorrection] = []
    facts: list[dict] = []

    for block in response.content:
        if block.type != "tool_use" or block.name != "soumettre_corrections_pass1":
            continue
        default_page = batch[0][0]
        for rc in block.input.get("corrections", []):
            c = _parse_correction(rc, PASS1_CATS, default_page)
            if c:
                corrections.append(c)
        for fi in block.input.get("fact_check_items", []):
            text = (fi.get("text") or "").strip()
            if not text or len(text) < 3:
                continue
            item_type = fi.get("item_type", "proper_noun")
            if item_type not in ("date", "proper_noun", "title"):
                item_type = "proper_noun"
            facts.append({
                "text": text,
                "context": (fi.get("context") or text)[:300],
                "item_type": item_type,
                "page_hint": int(fi.get("page_hint", batch[0][0] + 1)),
            })

    logger.info("Passe 1a lot pp.%d-%d : %d corrections, %d faits",
                batch[0][0] + 1, batch[-1][0] + 1, len(corrections), len(facts))
    return corrections, facts


# ── Passe 1b — Relecture rapide A/B/C ─────────────────────────────────────────

async def _process_relecture(
    client: anthropic.AsyncAnthropic,
    pages: list,
    already_found: list[ClaudeCorrection],
    doc_type: str,
    attempt: int = 0,
    metadata: dict | None = None,
    usage: _ApiUsage | None = None,
) -> list[ClaudeCorrection]:
    """
    Relecture ciblée sur A/B/C.
    Passe le texte complet + la liste des erreurs déjà trouvées pour éviter les doublons.
    """
    system = _build_system(_SYSTEM_RELECTURE, doc_type, metadata)
    full_text = _build_condensed_text(pages, RELECTURE_MAX_CHARS)

    # Contexte des erreurs déjà trouvées (A, B, C seulement)
    found_abc = [c for c in already_found if c.category in {"A", "B", "C"}]
    found_context = "\n".join(
        f"  p.{c.page_num + 1} [{c.category}] « {c.original_text[:60]} »"
        for c in found_abc[:100]
    )

    user = (
        f"ERREURS DÉJÀ TROUVÉES PAR LE PREMIER CORRECTEUR (ne pas répéter) :\n"
        f"{found_context or '(aucune pour linstant)'}\n\n"
        f"TEXTE DU DOCUMENT :\n{full_text}\n\n"
        "Appelle l'outil soumettre_corrections_pass1 avec les erreurs NOUVELLES uniquement "
        "(catégories A, B, C — ce qui a été manqué lors de la première passe)."
    )

    try:
        response = await client.messages.create(
            model=settings.CLAUDE_FAST_MODEL,
            max_tokens=4096,
            temperature=0,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS_PASS1,
            tool_choice={"type": "tool", "name": "soumettre_corrections_pass1"},
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.RateLimitError:
        if attempt < 2:
            wait = 30 * (attempt + 1)
            logger.warning("Rate limit Relecture — attente %ds…", wait)
            await asyncio.sleep(wait)
            return await _process_relecture(client, pages, already_found, doc_type, attempt + 1, metadata, usage)
        return []
    except Exception as exc:
        logger.error("Claude Relecture erreur : %s", exc)
        return []

    if usage is not None:
        usage.add(response)

    corrections: list[ClaudeCorrection] = []
    for block in response.content:
        if block.type != "tool_use" or block.name != "soumettre_corrections_pass1":
            continue
        for rc in block.input.get("corrections", []):
            cat = rc.get("category", "A")
            if cat not in {"A", "B", "C"}:
                continue
            c = _parse_correction(rc, {"A", "B", "C"}, 0)
            if c:
                corrections.append(c)

    logger.info("Passe 1b (relecture) : %d corrections supplémentaires A/B/C", len(corrections))
    return corrections


# ── Passe 2 — E/F/G document entier ──────────────────────────────────────────

async def _process_pass2_segment(
    client: anthropic.AsyncAnthropic,
    segment_text: str,
    system: str,
    segment_label: str,
    attempt: int = 0,
    usage: _ApiUsage | None = None,
) -> list[ClaudeCorrection]:
    """Exécute la Passe 2 sur un segment de texte unique."""
    user = (
        f"Voici {segment_label} avec numéros de page.\n"
        "Identifie TOUTES les erreurs E (Sémantique), F (Uniformisation), G (Renvois).\n"
        "Pour F : compare systématiquement chaque terme sur toutes les pages de ce segment.\n"
        "'corrected' = forme corrigée concrète OBLIGATOIRE.\n\n"
        f"{segment_text}\n\n"
        "Appelle maintenant l'outil soumettre_corrections_pass2."
    )
    try:
        response = await client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=8192,
            temperature=0,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS_PASS2,
            tool_choice={"type": "tool", "name": "soumettre_corrections_pass2"},
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.RateLimitError:
        if attempt < 2:
            wait = 30 * (attempt + 1)
            logger.warning("Rate limit Passe 2 %s — attente %ds…", segment_label, wait)
            await asyncio.sleep(wait)
            return await _process_pass2_segment(client, segment_text, system, segment_label, attempt + 1, usage)
        return []
    except Exception as exc:
        logger.error("Claude Passe 2 erreur (%s) : %s", segment_label, exc)
        return []

    if usage is not None:
        usage.add(response)

    if response.stop_reason == "max_tokens":
        logger.warning("Passe 2 %s : max_tokens — certaines corrections E/F/G peuvent manquer", segment_label)

    corrections: list[ClaudeCorrection] = []
    for block in response.content:
        if block.type != "tool_use" or block.name != "soumettre_corrections_pass2":
            continue
        for rc in block.input.get("corrections", []):
            c = _parse_correction(rc, PASS2_CATS, 0)
            if c:
                corrections.append(c)
    return corrections


async def _process_pass2(
    client: anthropic.AsyncAnthropic,
    pages: list,
    doc_type: str,
    attempt: int = 0,
    metadata: dict | None = None,
    usage: _ApiUsage | None = None,
) -> list[ClaudeCorrection]:
    """
    Passe 2 E/F/G — Fix A: sliding-window for long documents.

    - full_text <= PASS2_SINGLE_CALL_CHARS  →  single call (unchanged behaviour).
    - full_text >  PASS2_SINGLE_CALL_CHARS  →  overlapping chunks of PASS2_CHUNK_SIZE
      with PASS2_CHUNK_OVERLAP, capped at PASS2_MAX_CHUNKS to bound API cost.
      Chunks try to split on page boundaries; results are deduplicated by
      (page_num, category, original_text).
    """
    system = _build_system(_SYSTEM_PASS2_BASE, doc_type, metadata)
    full_text = _build_condensed_text(pages, MAX_FULL_TEXT_CHARS)

    if len(full_text) <= PASS2_SINGLE_CALL_CHARS:
        # Short document — single call (current behaviour)
        corrections = await _process_pass2_segment(
            client, full_text, system, "l'integralite du document", usage=usage
        )
        logger.info("Passe 2 (E/F/G) : %d corrections (appel unique)", len(corrections))
        return corrections

    # Long document — build overlapping chunks capped at PASS2_MAX_CHUNKS
    chunks: list[tuple[str, str]] = []
    offset = 0
    chunk_idx = 1
    total_chars = len(full_text)
    while offset < total_chars and chunk_idx <= PASS2_MAX_CHUNKS:
        end = min(offset + PASS2_CHUNK_SIZE, total_chars)
        # Prefer splitting on a page boundary to avoid cutting mid-page
        if end < total_chars:
            boundary = full_text.rfind("\n--- PAGE ", offset + PASS2_CHUNK_SIZE // 2, end)
            if boundary != -1:
                end = boundary
        chunk_text = full_text[offset:end]
        label = f"le segment {chunk_idx}/{min(PASS2_MAX_CHUNKS, -(-total_chars // PASS2_CHUNK_SIZE))} du document (chars {offset + 1}–{end})"
        chunks.append((chunk_text, label))
        offset = max(offset + 1, end - PASS2_CHUNK_OVERLAP)
        chunk_idx += 1

    if total_chars > offset:
        logger.info(
            "Passe 2 — document tronque a %d chunks (PASS2_MAX_CHUNKS=%d) ; ~%d chars analyses sur %d",
            len(chunks), PASS2_MAX_CHUNKS, chunks[-1][0].__len__() + sum(len(c) - PASS2_CHUNK_OVERLAP for c, _ in chunks[:-1]), total_chars,
        )

    logger.info(
        "Passe 2 — document long (%d chars) → %d chunks chevauchants (overlap=%d)",
        total_chars, len(chunks), PASS2_CHUNK_OVERLAP,
    )

    # Run chunks sequentially to avoid rate-limit bursts on long documents
    all_corrections: list[ClaudeCorrection] = []
    seen: set[tuple[int, str, str]] = set()
    for chunk_text, label in chunks:
        chunk_corrections = await _process_pass2_segment(client, chunk_text, system, label, usage=usage)
        for c in chunk_corrections:
            key = (c.page_num, c.category, c.original_text.lower().strip())
            if key not in seen:
                seen.add(key)
                all_corrections.append(c)
        if len(chunks) > 1:
            await asyncio.sleep(2)  # brief pause to avoid rate limits between chunks

    logger.info("Passe 2 (E/F/G) : %d corrections (%d chunks)", len(all_corrections), len(chunks))
    return all_corrections


# ── Passe 0 — Extraction métadonnées du document ─────────────────────────────

async def extract_doc_metadata(
    pages: list,
    doc_type: str = "autre",
    existing_metadata: dict | None = None,
) -> dict:
    """
    Passe 0 — Extraction légère des métadonnées (Haiku, ~500 tokens, ~3 s).

    Identifie sur les premières pages :
    - Les noms propres récurrents (personnages, acteurs, lieux fictifs) → exclusions
    - Les conventions typographiques spécifiques au document → house_rules

    Appelée depuis pipeline.py avant correct_document.
    Les champs déjà fournis par l'éditeur ne sont jamais écrasés.
    """
    existing = dict(existing_metadata) if existing_metadata else {}

    # Si l'éditeur a déjà rempli les deux champs clés, passer la passe 0
    if existing.get("characters") and existing.get("house_rules"):
        logger.info("Passe 0 : métadonnées complètes (éditeur) — skip")
        return existing

    sample_pages = [p for p in pages if p.text.strip()][:15]
    if not sample_pages:
        return existing

    sample_text = _build_condensed_text(sample_pages, 25_000)
    if not sample_text.strip():
        return existing

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    user = (
        "Analyse ce texte et retourne UNIQUEMENT ce JSON (sans markdown) :\n"
        '{"characters": "nom1, nom2, nom3", "house_rules": "convention1. convention2."}\n\n'
        "characters = noms récurrents (personnages, auteurs mentionnés, noms propres inhabituels) "
        "qui pourraient être signalés à tort comme fautes d'orthographe. Laisser vide si incertain.\n"
        "house_rules = conventions typographiques propres à CE document observées dans l'extrait "
        "(ex: 'les dialogues utilisent le tiret cadratin —', 'les titres de chapitres sont en majuscules'). "
        "Laisser vide si aucune convention spécifique n'est identifiable.\n\n"
        f"TEXTE :\n{sample_text}"
    )

    try:
        response = await client.messages.create(
            model=settings.CLAUDE_FAST_MODEL,
            max_tokens=512,
            temperature=0,
            system="Tu es un assistant éditorial. Analyse ce texte et réponds en JSON uniquement, sans aucune explication.",
            messages=[{"role": "user", "content": user}],
        )
        raw = (response.content[0].text if response.content else "").strip()
        # Retirer les balises markdown si présentes
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.MULTILINE).strip()

        import json as _json
        extracted = _json.loads(raw)

        result = dict(existing)
        if extracted.get("characters") and not result.get("characters"):
            result["characters"] = str(extracted["characters"])[:400]
        if extracted.get("house_rules") and not result.get("house_rules"):
            result["house_rules"] = str(extracted["house_rules"])[:400]

        logger.info(
            "Passe 0 — characters=%s | house_rules=%s",
            (result.get("characters") or "")[:80],
            (result.get("house_rules") or "")[:80],
        )
        return result

    except Exception as exc:
        logger.warning("Passe 0 (métadonnées) : échec silencieux — %s", exc)
        return existing


# ── Passe 1c — Zones grises (pages sans correction) ──────────────────────────

async def _recheck_empty_pages(
    client: anthropic.AsyncAnthropic,
    pages: list,
    already_found: list[ClaudeCorrection],
    doc_type: str,
    metadata: dict | None = None,
    usage: _ApiUsage | None = None,
    progress_callback=None,
) -> list[ClaudeCorrection]:
    """
    Passe 1c — Relecture ciblée des pages sans aucune correction A/B/C/D détectée.

    Ces pages ont été vues par la Passe 1a (et 1b) mais n'ont produit aucune correction.
    Elles sont renvoyées à Claude avec un signal explicite d'attention renforcée.
    Coût marginal (plafond 30 pages / 6 lots), gain potentiel élevé sur les oublis.
    """
    corrected_pages = {c.page_num for c in already_found if c.category in PASS1_CATS}
    all_page_nums = {p.page_num for p in pages if p.text.strip()}
    empty_pages = sorted(all_page_nums - corrected_pages)

    # Pas de zones grises si le doc est court (< 8 pages) ou si toutes les pages ont des corrections
    if not empty_pages or len(pages) < 8:
        logger.info("Passe 1c (zones grises) : skip (%d pages vides, %d total)", len(empty_pages), len(pages))
        return []

    # Plafonner à 30 pages pour limiter le coût
    empty_pages = empty_pages[:30]
    empty_page_data = [
        (p.page_num, p.text)
        for p in pages
        if p.page_num in set(empty_pages) and p.text.strip()
    ]

    logger.info("Passe 1c (zones grises) : %d pages sans correction A/B/C/D → relecture", len(empty_page_data))

    # Signaler Passe 1c au progress_callback (batch_idx=-1 = signal spécial Passe 1c)
    if progress_callback:
        try:
            await progress_callback(-1, -1)
        except Exception:  # noqa: BLE001
            pass

    system = _build_system(_SYSTEM_PASS1_BASE, doc_type, metadata)
    all_corrections: list[ClaudeCorrection] = []
    ZONE_BATCH = 5

    for i in range(0, len(empty_page_data), ZONE_BATCH):
        batch = empty_page_data[i:i + ZONE_BATCH]
        parts = [
            "Ces pages ont été analysées lors d'une première passe mais AUCUNE correction n'y a été signalée.\n"
            "Examine-les PARTICULIÈREMENT avec attention — y a-t-il des erreurs qui auraient pu être manquées ?\n"
            "Si ces pages sont réellement sans erreur, appelle l'outil avec corrections=[].\n\n"
        ]
        for page_num, text in batch:
            parts.append(f"--- PAGE {page_num + 1} ---\n{text}\n\n")
        parts.append("Appelle maintenant l'outil soumettre_corrections_pass1.")

        try:
            response = await client.messages.create(
                model=settings.CLAUDE_FAST_MODEL,
                max_tokens=4096,
                temperature=0,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=TOOLS_PASS1,
                tool_choice={"type": "tool", "name": "soumettre_corrections_pass1"},
                messages=[{"role": "user", "content": "".join(parts)}],
            )
            if usage is not None:
                usage.add(response)

            for block in response.content:
                if block.type != "tool_use" or block.name != "soumettre_corrections_pass1":
                    continue
                default_page = batch[0][0]
                for rc in block.input.get("corrections", []):
                    c = _parse_correction(rc, PASS1_CATS, default_page)
                    if c:
                        all_corrections.append(c)

        except anthropic.RateLimitError:
            logger.warning("Passe 1c lot %d : rate limit — skip", i // ZONE_BATCH)
        except Exception as exc:
            logger.warning("Passe 1c lot %d : erreur — %s", i // ZONE_BATCH, exc)

    logger.info("Passe 1c (zones grises) : %d corrections supplémentaires A/B/C/D", len(all_corrections))
    return all_corrections


# ── Point d'entrée public ──────────────────────────────────────────────────────

async def correct_document(
    pages: list,
    proper_noun_variants: dict[str, list[str]] | None = None,
    doc_type: str = "autre",
    progress_callback=None,
    enabled_categories: set[str] | None = None,
    metadata: dict | None = None,
) -> tuple[list[ClaudeCorrection], list[dict], _ApiUsage]:
    """
    Correction éditoriale — triple passe, temperature=0.
    Retourne (corrections_A_à_G, fact_check_items_pour_fact-checker H).

    Optimisation tokens : seules les passes utiles au preset sont exécutées.
      - ABCD-only : Pass 1a + 1b uniquement
      - EFG-only  : Pass 2 uniquement
      - Complet   : Pass 1a + 1b + Pass 2
      - H-only    : aucune passe (les items viennent de l'extraction dans run_pipeline)

    Reproductibilité : le prompt Claude ne contient PLUS de fp_patterns (retiré de
    pipeline.py). Avec temperature=0 et un prompt stable, même document → même output.
    """
    enabled_cats = set(enabled_categories) if enabled_categories else set("ABCDEFGH")
    PASS1_NEEDED = bool(enabled_cats & {"A", "B", "C", "D"})
    PASS2_NEEDED = bool(enabled_cats & {"E", "F", "G"})

    if not PASS1_NEEDED and not PASS2_NEEDED:
        # H-only ou preset vide : pas de passe Claude nécessaire ici
        logger.info("correct_document : aucune passe Claude (H-only ou preset vide)")
        return [], [], _ApiUsage()

    usage = _ApiUsage()
    client = anthropic.AsyncAnthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        default_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
    )
    pn_variants = proper_noun_variants or {}

    non_empty = [(p.page_num, p.text) for p in pages if p.text.strip()]
    total_pages = len(pages)

    # Adaptive batch size — réduit le nombre d'appels API pour les longs documents
    if total_pages <= 30:
        batch_size = 3
    elif total_pages <= 100:
        batch_size = 4
    else:
        batch_size = 5

    # Overlap de 1 page entre batches consécutifs pour les docs > 30 pages.
    # Les erreurs aux frontières de lots sont vues par 2 passes → déduplication ensuite.
    # Coût : +25–33 % d'appels Passe 1a, gain recall aux frontières.
    # Pour les petits docs (batch_size=3, ≤30 pages), pas d'overlap — inutile sur des lots si courts.
    stride = batch_size - 1 if batch_size >= 4 else batch_size
    batches = [non_empty[i: i + batch_size] for i in range(0, len(non_empty), stride)]
    batches = [b for b in batches if b]

    logger.info(
        "correct_document — %d pages non vides | lots de %d (stride=%d) → %d lots | "
        "P1=%s P2=%s | doc_type=%s",
        len(non_empty), batch_size, stride, len(batches), PASS1_NEEDED, PASS2_NEEDED, doc_type,
    )

    all_corrections: list[ClaudeCorrection] = []
    all_facts: list[dict] = []

    # ── Passe 1a : A/B/C/D par lots — parallèle avec semaphore ───────────────────
    if PASS1_NEEDED:
        effective_concurrent = 2 if total_pages > 80 else MAX_CONCURRENT_BATCHES
        sem = asyncio.Semaphore(effective_concurrent)
        logger.info("Passe 1a — concurrence : %d slots", effective_concurrent)
        batches_counter: list[int] = [0]

        async def process_with_sem(batch: list, idx: int) -> tuple[list[ClaudeCorrection], list[dict]]:
            async with sem:
                result = await _process_batch_pass1(client, batch, pn_variants, doc_type, metadata=metadata, usage=usage)
                batches_counter[0] += 1
                if progress_callback:
                    await progress_callback(batches_counter[0], len(batches))
                return result

        tasks = [process_with_sem(batch, i) for i, batch in enumerate(batches)]
        results = await asyncio.gather(*tasks)

        seen_facts: set[tuple[str, str]] = set()
        for corrections, facts in results:
            # Validation : Pass 1a ne retourne que A/B/C/D
            valid_p1 = [c for c in corrections if c.category in {"A", "B", "C", "D"}]
            if len(valid_p1) < len(corrections):
                logger.warning("Passe 1a : %d correction(s) hors A/B/C/D ignorée(s)",
                               len(corrections) - len(valid_p1))
            all_corrections.extend(valid_p1)
            for fi in facts:
                key = (fi["text"].lower(), fi["item_type"])
                if key not in seen_facts:
                    seen_facts.add(key)
                    all_facts.append(fi)

        logger.info("Passe 1a terminée : %d corrections A/B/C/D, %d faits à vérifier",
                    len(all_corrections), len(all_facts))

        # ── Passe 1b : Relecture ciblée A/B/C ────────────────────────────────────
        abc_count = sum(1 for c in all_corrections if c.category in {"A", "B", "C"})
        logger.info("Passe 1b — Relecture A/B/C (%d erreurs A/B/C)…", abc_count)
        if progress_callback:
            await progress_callback(len(batches) + 1, len(batches) + 1)
        relecture_corrections = await _process_relecture(
            client, pages, all_corrections, doc_type, metadata=metadata, usage=usage,
        )
        # Validation : relecture ne retourne que A/B/C
        valid_relecture = [c for c in relecture_corrections if c.category in {"A", "B", "C"}]
        if len(valid_relecture) < len(relecture_corrections):
            logger.warning("Passe 1b : %d correction(s) hors A/B/C ignorée(s)",
                           len(relecture_corrections) - len(valid_relecture))
        all_corrections.extend(valid_relecture)
        logger.info("Après Passe 1b : %d corrections au total", len(all_corrections))

        # ── Passe 1c : Zones grises — pages sans aucune correction ───────────────
        grey_corrections = await _recheck_empty_pages(
            client, pages, all_corrections, doc_type, metadata=metadata, usage=usage,
            progress_callback=progress_callback,
        )
        valid_grey = [c for c in grey_corrections if c.category in PASS1_CATS]
        if len(valid_grey) < len(grey_corrections):
            logger.warning("Passe 1c : %d correction(s) hors A/B/C/D ignorée(s)",
                           len(grey_corrections) - len(valid_grey))
        all_corrections.extend(valid_grey)
        if valid_grey:
            logger.info("Après Passe 1c (zones grises) : %d corrections au total", len(all_corrections))

    # ── Passe 2 : E/F/G document entier ──────────────────────────────────────────
    if PASS2_NEEDED:
        logger.info("Passe 2 — E/F/G document entier…")
        pass2_corrections = await _process_pass2(client, pages, doc_type, metadata=metadata, usage=usage)
        # Validation : Pass 2 ne retourne que E/F/G
        valid_p2 = [c for c in pass2_corrections if c.category in {"E", "F", "G"}]
        if len(valid_p2) < len(pass2_corrections):
            logger.warning("Passe 2 : %d correction(s) hors E/F/G ignorée(s)",
                           len(pass2_corrections) - len(valid_p2))
        all_corrections.extend(valid_p2)

    logger.info(
        "correct_document terminée : %d corrections (A-G), %d faits à vérifier | "
        "tokens in=%d out=%d cache_read=%d cache_write=%d coût≈$%.4f",
        len(all_corrections), len(all_facts),
        usage.input_tokens, usage.output_tokens,
        usage.cache_read_tokens, usage.cache_write_tokens,
        usage.cost_usd(),
    )
    return all_corrections, all_facts, usage
