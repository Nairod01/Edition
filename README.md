# Outil d'édition professionnelle de manuscrits PDF

Analyse un PDF (manuscrit ou livre maquetté) et injecte des **annotations éditoriales natives Adobe Acrobat** directement dans le fichier.

## Fonctionnalités

| Axe d'analyse | Ce qui est détecté |
|---|---|
| **Orthographe & Grammaire** | Fautes, accords, conjugaisons, homophones |
| **Typographie française** | Espaces insécables, guillemets, tirets, points de suspension |
| **Style & Lisibilité** | Répétitions, phrases lourdes, passif excessif, clichés, pléonasmes |
| **Homogénéisation** | Noms propres, vouvoiement, temps narratifs, style des dialogues |
| **Structure narrative** | Équilibre chapitres, transitions, POV, analepses, contradictions |
| **Maquette** | Hiérarchie des titres, veuves/orphelines, numérotation, en-têtes |

### Code couleur des annotations

| Couleur | Catégorie |
|---|---|
| 🔴 Rouge | Orthographe & Grammaire |
| 🟠 Orange | Typographie |
| 🟡 Jaune | Style & Lisibilité |
| 🔵 Bleu | Cohérence & Homogénéisation |
| 🟣 Violet | Structure narrative |
| 🟢 Vert | Maquette & Formatage |

## Installation

```bash
pip install -r requirements.txt
```

Définir la clé API Anthropic :
```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Utilisation

### Analyse complète

```bash
python main.py analyse manuscrit.pdf
```

Génère :
- `manuscrit_annoté.pdf` — PDF avec annotations Adobe
- `manuscrit_rapport.html` — Rapport éditorial complet

### Options avancées

```bash
# Avec configuration projet
python main.py analyse manuscrit.pdf --config mon_projet.yaml

# Nommer les fichiers de sortie
python main.py analyse manuscrit.pdf --output corrections.pdf --rapport rapport.html

# Mode rapide (sans page de légende)
python main.py analyse manuscrit.pdf --sans-legende

# Inspecter la structure sans analyser
python main.py inspecter manuscrit.pdf
```

### Inspecter la structure du document

```bash
python main.py inspecter manuscrit.pdf
```

Affiche les chapitres détectés, polices, mises en page — utile avant de configurer `config.yaml`.

## Configuration

Copiez `config.yaml` et adaptez-le à chaque projet :

```yaml
genre: "roman"           # roman | essai | documentaire | jeunesse | technique
niveau: "standard"       # leger | standard | approfondi
temps_narratif: "passe"  # passe | present | mixte

noms_propres:
  - "Marthe"             # Noms propres connus (évite les faux positifs)
  - "Le Havre"

regles_maison:
  guillemets: "français"        # français (« ») | anglais ("")
  tirets_dialogue: "cadratin"   # cadratin (—) | demi-cadratin (–)

analyses:
  maquette: false        # Désactiver une catégorie si non pertinente
```

## Architecture

```
src/
  extractor.py    — Extraction du texte avec positions (PyMuPDF)
  rules.py        — Règles typo françaises locales (regex, instantané)
  analyzer.py     — Analyse éditoriale via Claude API
  annotator.py    — Injection des annotations dans le PDF
  reporter.py     — Génération du rapport HTML
main.py           — CLI (Typer)
config.yaml       — Configuration par projet
tests/            — Tests des règles typographiques
```

## Compatibilité des annotations

Les annotations sont des **annotations PDF natives** (standard ISO 32000) :
- Adobe Acrobat Reader / Pro
- PDF Expert (iOS/macOS)
- Foxit Reader
- Okular (Linux)
- Aperçu (macOS, partiellement)

## Traitement des longs documents

- Chunking intelligent par paragraphes (jamais de coupure de phrase)
- Chevauchement entre chunks pour ne pas rater les problèmes aux jonctions
- Analyses parallèles (orthographe + style + cohérence simultanément)
- Cache des analyses pour éviter les re-traitements
