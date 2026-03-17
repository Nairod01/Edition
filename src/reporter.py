"""
Génération du rapport éditorial HTML.
Rapport standalone exportable pour le retour client.
"""

from __future__ import annotations

import html
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .analyzer import EditorialIssue
from .annotator import ANNOTATION_STYLES, _DEFAULT_STYLE
from .extractor import DocumentStructure


# ─────────────────────────────────────────────
# Template HTML
# ─────────────────────────────────────────────

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Rapport éditorial — {doc_title}</title>
<style>
  :root {{
    --rouge: #e01010;
    --orange: #e06000;
    --jaune: #b09000;
    --bleu: #1464d8;
    --violet: #7010c0;
    --vert: #107820;
    --gris-clair: #f5f5f5;
    --gris: #888;
    --texte: #1a1a1a;
    --border: #ddd;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: 'Georgia', serif;
    color: var(--texte);
    background: #fafafa;
    line-height: 1.6;
  }}

  header {{
    background: #1a1a2e;
    color: white;
    padding: 2rem 3rem;
    border-bottom: 4px solid #e06000;
  }}

  header h1 {{ font-size: 1.6rem; font-weight: normal; letter-spacing: 0.5px; }}
  header .subtitle {{ color: #aaa; margin-top: 0.3rem; font-size: 0.9rem; font-family: sans-serif; }}

  main {{ max-width: 1100px; margin: 0 auto; padding: 2rem; }}

  /* Résumé chiffré */
  .summary-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 1rem;
    margin: 2rem 0;
  }}

  .summary-card {{
    background: white;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 1.2rem;
    text-align: center;
    border-top: 4px solid;
  }}

  .summary-card .count {{ font-size: 2.2rem; font-weight: bold; font-family: sans-serif; }}
  .summary-card .label {{ font-size: 0.8rem; color: var(--gris); font-family: sans-serif; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 0.3rem; }}

  .card-orthographe   {{ border-top-color: var(--rouge); }}   .card-orthographe   .count {{ color: var(--rouge); }}
  .card-typographie   {{ border-top-color: var(--orange); }}  .card-typographie   .count {{ color: var(--orange); }}
  .card-style         {{ border-top-color: var(--jaune); }}   .card-style         .count {{ color: var(--jaune); }}
  .card-homogeneisation {{ border-top-color: var(--bleu); }}  .card-homogeneisation .count {{ color: var(--bleu); }}
  .card-structure     {{ border-top-color: var(--violet); }}  .card-structure     .count {{ color: var(--violet); }}
  .card-maquette      {{ border-top-color: var(--vert); }}    .card-maquette      .count {{ color: var(--vert); }}
  .card-total         {{ border-top-color: #333; }}           .card-total         .count {{ color: #333; }}

  /* Filtres */
  .filters {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin: 1.5rem 0;
    font-family: sans-serif;
  }}

  .filter-btn {{
    border: 1px solid var(--border);
    background: white;
    padding: 0.4rem 1rem;
    border-radius: 20px;
    cursor: pointer;
    font-size: 0.85rem;
    transition: all 0.15s;
  }}

  .filter-btn:hover, .filter-btn.active {{ color: white; border-color: transparent; }}
  .filter-btn[data-cat="orthographe"]:hover,     .filter-btn[data-cat="orthographe"].active     {{ background: var(--rouge); }}
  .filter-btn[data-cat="typographie"]:hover,     .filter-btn[data-cat="typographie"].active     {{ background: var(--orange); }}
  .filter-btn[data-cat="style"]:hover,           .filter-btn[data-cat="style"].active           {{ background: var(--jaune); }}
  .filter-btn[data-cat="homogeneisation"]:hover, .filter-btn[data-cat="homogeneisation"].active {{ background: var(--bleu); }}
  .filter-btn[data-cat="structure"]:hover,       .filter-btn[data-cat="structure"].active       {{ background: var(--violet); }}
  .filter-btn[data-cat="maquette"]:hover,        .filter-btn[data-cat="maquette"].active        {{ background: var(--vert); }}
  .filter-btn[data-cat="all"]:hover,             .filter-btn[data-cat="all"].active             {{ background: #333; }}

  /* Table des issues */
  .issues-table {{
    width: 100%;
    border-collapse: collapse;
    background: white;
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
    font-size: 0.9rem;
  }}

  .issues-table th {{
    background: #f0f0f0;
    padding: 0.7rem 1rem;
    text-align: left;
    font-family: sans-serif;
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--gris);
    border-bottom: 1px solid var(--border);
  }}

  .issues-table td {{
    padding: 0.75rem 1rem;
    border-bottom: 1px solid #f0f0f0;
    vertical-align: top;
  }}

  .issues-table tr:last-child td {{ border-bottom: none; }}
  .issues-table tr:hover td {{ background: var(--gris-clair); }}

  .badge {{
    display: inline-block;
    padding: 0.2rem 0.6rem;
    border-radius: 12px;
    font-size: 0.75rem;
    font-family: sans-serif;
    font-weight: bold;
    color: white;
    white-space: nowrap;
  }}

  .badge-orthographe   {{ background: var(--rouge); }}
  .badge-typographie   {{ background: var(--orange); }}
  .badge-style         {{ background: var(--jaune); color: #333; }}
  .badge-homogeneisation {{ background: var(--bleu); }}
  .badge-structure     {{ background: var(--violet); }}
  .badge-maquette      {{ background: var(--vert); }}

  .severity-error      {{ color: var(--rouge); font-weight: bold; font-family: sans-serif; font-size: 0.8rem; }}
  .severity-warning    {{ color: var(--orange); font-family: sans-serif; font-size: 0.8rem; }}
  .severity-suggestion {{ color: var(--gris); font-family: sans-serif; font-size: 0.8rem; }}

  .snippet {{
    font-family: 'Courier New', monospace;
    background: #f5f5f5;
    padding: 0.15rem 0.4rem;
    border-radius: 3px;
    font-size: 0.85rem;
    color: #333;
  }}

  .correction {{
    font-family: 'Courier New', monospace;
    background: #e8f5e9;
    padding: 0.15rem 0.4rem;
    border-radius: 3px;
    font-size: 0.85rem;
    color: var(--vert);
  }}

  .page-num {{
    font-family: sans-serif;
    font-weight: bold;
    color: #555;
    font-size: 0.85rem;
  }}

  /* Statistiques par chapitre */
  .chapter-stats {{ margin: 2rem 0; }}
  .chapter-stats h2 {{ font-size: 1.2rem; margin-bottom: 1rem; }}

  .chapter-row {{
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-bottom: 0.5rem;
    font-family: sans-serif;
    font-size: 0.85rem;
  }}

  .chapter-name {{ width: 150px; color: var(--gris); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .chapter-bar-container {{ flex: 1; background: #eee; border-radius: 3px; height: 16px; }}
  .chapter-bar {{ height: 16px; border-radius: 3px; background: linear-gradient(90deg, var(--rouge), var(--orange)); }}
  .chapter-count {{ width: 40px; text-align: right; font-weight: bold; }}

  /* Entités détectées */
  .entities-section {{ margin: 2rem 0; }}
  .entity-list {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.5rem; }}
  .entity-tag {{
    background: #e8eaf6;
    color: #3949ab;
    padding: 0.2rem 0.7rem;
    border-radius: 12px;
    font-family: sans-serif;
    font-size: 0.8rem;
  }}

  footer {{
    text-align: center;
    color: var(--gris);
    font-size: 0.8rem;
    font-family: sans-serif;
    padding: 2rem;
    border-top: 1px solid var(--border);
    margin-top: 3rem;
  }}
</style>
</head>
<body>

<header>
  <h1>Rapport éditorial — {doc_title}</h1>
  <div class="subtitle">Analysé le {date} · {page_count} pages · {total_issues} remarques éditoriales</div>
</header>

<main>

  <!-- Résumé chiffré -->
  <div class="summary-grid">
    <div class="summary-card card-total">
      <div class="count">{total_issues}</div>
      <div class="label">Total</div>
    </div>
    {summary_cards}
  </div>

  <!-- Entités détectées -->
  {entities_section}

  <!-- Distribution par chapitre -->
  {chapter_section}

  <!-- Filtres -->
  <div class="filters">
    <button class="filter-btn active" data-cat="all" onclick="filterIssues('all')">Tout afficher</button>
    <button class="filter-btn" data-cat="orthographe"      onclick="filterIssues('orthographe')">🔴 Orthographe</button>
    <button class="filter-btn" data-cat="typographie"      onclick="filterIssues('typographie')">🟠 Typographie</button>
    <button class="filter-btn" data-cat="style"            onclick="filterIssues('style')">🟡 Style</button>
    <button class="filter-btn" data-cat="homogeneisation"  onclick="filterIssues('homogeneisation')">🔵 Cohérence</button>
    <button class="filter-btn" data-cat="structure"        onclick="filterIssues('structure')">🟣 Structure</button>
    <button class="filter-btn" data-cat="maquette"         onclick="filterIssues('maquette')">🟢 Maquette</button>
  </div>

  <!-- Table des issues -->
  <table class="issues-table" id="issues-table">
    <thead>
      <tr>
        <th>Page</th>
        <th>Catégorie</th>
        <th>Sévérité</th>
        <th>Passage concerné</th>
        <th>Remarque éditoriale</th>
        <th>Suggestion</th>
      </tr>
    </thead>
    <tbody>
      {table_rows}
    </tbody>
  </table>

</main>

<footer>
  Rapport généré automatiquement par l'outil d'édition IA · {date}
</footer>

<script>
function filterIssues(cat) {{
  const rows = document.querySelectorAll('#issues-table tbody tr');
  rows.forEach(row => {{
    row.style.display = (cat === 'all' || row.dataset.cat === cat) ? '' : 'none';
  }});
  document.querySelectorAll('.filter-btn').forEach(btn => {{
    btn.classList.toggle('active', btn.dataset.cat === cat);
  }});
}}
</script>

</body>
</html>"""


# ─────────────────────────────────────────────
# Générateur de rapport
# ─────────────────────────────────────────────

class HTMLReporter:
    """Génère un rapport HTML des issues éditoriales."""

    def __init__(self, structure: DocumentStructure, issues: list[EditorialIssue]):
        self.structure = structure
        self.issues = sorted(issues, key=lambda i: (i.page_num, i.category, i.severity))

    def generate(self, output_path: str | Path):
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        html_content = _HTML_TEMPLATE.format(
            doc_title=html.escape(self.structure.title),
            date=datetime.now().strftime("%d %B %Y à %H:%M"),
            page_count=self.structure.page_count,
            total_issues=len(self.issues),
            summary_cards=self._render_summary_cards(),
            entities_section=self._render_entities_section(),
            chapter_section=self._render_chapter_section(),
            table_rows=self._render_table_rows(),
        )

        output_path.write_text(html_content, encoding="utf-8")

    # ── Sections HTML ────────────────────────

    def _render_summary_cards(self) -> str:
        by_cat: dict[str, int] = defaultdict(int)
        for issue in self.issues:
            by_cat[issue.category] += 1

        cards = []
        cat_labels = {
            "orthographe":      "Orthographe",
            "typographie":      "Typographie",
            "style":            "Style",
            "homogeneisation":  "Cohérence",
            "structure":        "Structure",
            "maquette":         "Maquette",
        }
        for cat, label in cat_labels.items():
            count = by_cat.get(cat, 0)
            cards.append(f"""
    <div class="summary-card card-{cat}">
      <div class="count">{count}</div>
      <div class="label">{label}</div>
    </div>""")

        return "\n".join(cards)

    def _render_entities_section(self) -> str:
        entities = self.structure.entities
        if not entities:
            return ""

        parts = ["<div class='entities-section'><h2>Entités détectées dans le document</h2>"]

        labels = {
            "personnages": "Personnages",
            "lieux": "Lieux",
            "termes_specifiques": "Termes spécifiques",
        }
        for key, label in labels.items():
            items = entities.get(key, [])
            if items:
                tags = "".join(f"<span class='entity-tag'>{html.escape(str(i))}</span>" for i in items[:50])
                parts.append(f"<p><strong>{label} :</strong></p><div class='entity-list'>{tags}</div>")

        parts.append("</div>")
        return "\n".join(parts)

    def _render_chapter_section(self) -> str:
        if not self.structure.chapter_pages:
            return ""

        # Répartition des issues par tranche de pages (chapitres)
        chapter_pages = sorted(set(self.structure.chapter_pages))
        chapter_pages.append(self.structure.page_count + 1)  # sentinelle

        chapters: list[tuple[int, int, int]] = []  # (start, end, count)
        for i in range(len(chapter_pages) - 1):
            start = chapter_pages[i] + 1
            end = chapter_pages[i + 1]
            count = sum(1 for issue in self.issues if start <= issue.page_num < end)
            chapters.append((start, end, count))

        if not chapters:
            return ""

        max_count = max(c for _, _, c in chapters) or 1

        rows = []
        for i, (start, end, count) in enumerate(chapters):
            width = int((count / max_count) * 100)
            rows.append(f"""
      <div class="chapter-row">
        <div class="chapter-name">Chap. {i + 1} (p.{start}–{end})</div>
        <div class="chapter-bar-container">
          <div class="chapter-bar" style="width:{width}%"></div>
        </div>
        <div class="chapter-count">{count}</div>
      </div>""")

        return f"""
    <div class="chapter-stats">
      <h2>Distribution des remarques par chapitre</h2>
      {"".join(rows)}
    </div>"""

    def _render_table_rows(self) -> str:
        rows = []
        severity_labels = {
            "error": ("error", "Faute"),
            "warning": ("warning", "Avertissement"),
            "suggestion": ("suggestion", "Suggestion"),
        }

        for issue in self.issues:
            sev_class, sev_label = severity_labels.get(issue.severity, ("warning", issue.severity))
            snippet_escaped = html.escape(issue.text_snippet[:80])
            message_escaped = html.escape(issue.message)
            correction_html = (
                f"<span class='correction'>→ {html.escape(issue.correction[:100])}</span>"
                if issue.correction and issue.correction.strip()
                else "<span style='color:#ccc'>—</span>"
            )

            rows.append(f"""
      <tr data-cat="{issue.category}">
        <td><span class="page-num">p. {issue.page_num}</span></td>
        <td><span class="badge badge-{issue.category}">{issue.category}</span></td>
        <td><span class="severity-{sev_class}">{sev_label}</span></td>
        <td><span class="snippet">{snippet_escaped}</span></td>
        <td>{message_escaped}</td>
        <td>{correction_html}</td>
      </tr>""")

        return "\n".join(rows)
