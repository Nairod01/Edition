#!/usr/bin/env python3
"""
Outil d'édition professionnelle de manuscrits PDF
==================================================
Analyse un PDF (manuscrit ou livre maquetté) et injecte
des commentaires éditoriaux directement dans le fichier.

Usage :
    python main.py analyse manuscrit.pdf
    python main.py analyse manuscrit.pdf --output annoté.pdf --config mon_projet.yaml
    python main.py rapport  manuscrit.pdf --output rapport.html
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint

from src.extractor import PDFExtractor
from src.analyzer import EditorialAnalyzer
from src.annotator import PDFAnnotator
from src.reporter import HTMLReporter

# ─────────────────────────────────────────────
# Init
# ─────────────────────────────────────────────

app = typer.Typer(
    name="editrice",
    help="Outil d'édition professionnelle de manuscrits PDF — propulsé par Claude",
    add_completion=False,
    rich_markup_mode="rich",
)

console = Console()

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s  %(name)s  %(message)s",
)


# ─────────────────────────────────────────────
# Commande principale : analyse
# ─────────────────────────────────────────────

@app.command()
def analyse(
    input_pdf: Path = typer.Argument(..., help="PDF à analyser (manuscrit ou livre maquetté)"),
    output: Path = typer.Option(None, "--output", "-o", help="Fichier PDF annoté en sortie"),
    config: Path = typer.Option(None, "--config", "-c", help="Fichier de configuration YAML"),
    rapport: Path = typer.Option(None, "--rapport", "-r", help="Génère aussi un rapport HTML"),
    api_key: str = typer.Option(None, "--api-key", envvar="ANTHROPIC_API_KEY", help="Clé API Anthropic"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Affiche les logs détaillés"),
    legende: bool = typer.Option(True, "--legende/--sans-legende", help="Ajoute une page de légende au début"),
):
    """
    [bold]Analyse un manuscrit PDF et annote les problèmes éditoriaux.[/bold]

    Effectue plusieurs passes d'analyse :
    orthographe, typographie, style, cohérence, structure, maquette.
    Les commentaires sont injectés directement dans le PDF (compatibles Adobe Acrobat).
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Vérifications
    if not input_pdf.exists():
        console.print(f"[red]Erreur :[/red] fichier introuvable : {input_pdf}")
        raise typer.Exit(1)

    if input_pdf.suffix.lower() != ".pdf":
        console.print("[red]Erreur :[/red] le fichier doit être un PDF.")
        raise typer.Exit(1)

    # Clé API
    resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not resolved_key:
        console.print(
            "[red]Erreur :[/red] clé API Anthropic manquante.\n"
            "Définissez la variable d'environnement [bold]ANTHROPIC_API_KEY[/bold] "
            "ou passez [bold]--api-key[/bold]."
        )
        raise typer.Exit(1)

    # Chemins de sortie par défaut
    if output is None:
        output = input_pdf.parent / (input_pdf.stem + "_annoté.pdf")
    if rapport is None:
        rapport = input_pdf.parent / (input_pdf.stem + "_rapport.html")

    # Configuration
    cfg = _load_config(config)

    console.print(Panel.fit(
        f"[bold]Outil d'édition professionnelle[/bold]\n"
        f"Fichier : [cyan]{input_pdf.name}[/cyan]\n"
        f"Sortie  : [cyan]{output.name}[/cyan]\n"
        f"Modèle  : [cyan]{cfg.get('modele', 'claude-opus-4-6')}[/cyan]\n"
        f"Niveau  : [cyan]{cfg.get('niveau', 'standard')}[/cyan]",
        title="[bold yellow]Analyse éditoriale[/bold yellow]",
    ))

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:

        # ── Étape 1 : Extraction ─────────────────
        task1 = progress.add_task("[cyan]Extraction du contenu PDF…", total=1)
        extractor = PDFExtractor(input_pdf)
        structure = extractor.extract()

        scanned_pages = sum(1 for p in structure.pages if p.is_scanned)
        if scanned_pages > 0:
            console.print(
                f"[yellow]Avertissement :[/yellow] {scanned_pages} page(s) semblent être des images scannées "
                f"et ne pourront pas être analysées sans OCR."
            )

        chunks = extractor.get_chunks(
            structure,
            max_tokens=cfg.get("taille_chunk_tokens", 2000),
            overlap_tokens=cfg.get("chevauchement_tokens", 200),
        )
        extractor.close()
        progress.update(task1, completed=1,
                        description=f"[green]✓ Extraction : {structure.page_count} pages, {len(chunks)} chunks")

        console.print(
            f"  Structure : [bold]{structure.page_count}[/bold] pages · "
            f"[bold]{len(structure.chapter_pages)}[/bold] chapitre(s) détecté(s) · "
            f"[bold]{len(chunks)}[/bold] chunks d'analyse"
        )

        # ── Étape 2 : Analyse éditoriale ─────────
        task2 = progress.add_task("[cyan]Analyse éditoriale en cours…", total=1)
        analyzer = EditorialAnalyzer(cfg, api_key=resolved_key)

        try:
            issues = analyzer.analyze(structure, chunks)
        except Exception as e:
            console.print(f"[red]Erreur lors de l'analyse :[/red] {e}")
            raise typer.Exit(1)

        progress.update(task2, completed=1,
                        description=f"[green]✓ Analyse : {len(issues)} remarques éditoriales")

        # ── Étape 3 : Annotation du PDF ──────────
        task3 = progress.add_task("[cyan]Injection des annotations…", total=1)
        annotator = PDFAnnotator(input_pdf, output)
        stats = annotator.annotate(issues)

        if legende:
            annotator.add_legend_page()

        annotator.save()
        annotator.close()
        progress.update(task3, completed=1,
                        description=f"[green]✓ Annotations : {stats.placed} positionnées, {stats.fallback} en marge")

        # ── Étape 4 : Rapport HTML ───────────────
        task4 = progress.add_task("[cyan]Génération du rapport HTML…", total=1)
        reporter = HTMLReporter(structure, issues)
        reporter.generate(rapport)
        progress.update(task4, completed=1,
                        description=f"[green]✓ Rapport HTML généré")

    # ── Résumé final ─────────────────────────
    console.print()
    _print_summary(issues, stats, output, rapport)


# ─────────────────────────────────────────────
# Commande : rapport seul (sans ré-analyse)
# ─────────────────────────────────────────────

@app.command()
def rapport_only(
    input_pdf: Path = typer.Argument(..., help="PDF annoté à partir duquel générer le rapport"),
    output: Path = typer.Option(None, "--output", "-o", help="Fichier HTML de sortie"),
    config: Path = typer.Option(None, "--config", "-c"),
):
    """
    Génère uniquement le rapport HTML à partir d'un PDF déjà annoté
    (utile pour régénérer le rapport sans relancer l'analyse complète).
    """
    if not input_pdf.exists():
        console.print(f"[red]Erreur :[/red] fichier introuvable : {input_pdf}")
        raise typer.Exit(1)

    if output is None:
        output = input_pdf.parent / (input_pdf.stem + "_rapport.html")

    cfg = _load_config(config)

    with console.status("[cyan]Extraction de la structure…"):
        extractor = PDFExtractor(input_pdf)
        structure = extractor.extract()
        extractor.close()

    reporter = HTMLReporter(structure, [])
    reporter.generate(output)
    console.print(f"[green]✓[/green] Rapport généré : [cyan]{output}[/cyan]")


# ─────────────────────────────────────────────
# Commande : inspecter (affiche la structure)
# ─────────────────────────────────────────────

@app.command()
def inspecter(
    input_pdf: Path = typer.Argument(..., help="PDF à inspecter"),
):
    """
    Affiche la structure détectée du document (titres, chapitres, polices)
    sans lancer d'analyse. Utile pour vérifier la configuration avant analyse.
    """
    if not input_pdf.exists():
        console.print(f"[red]Erreur :[/red] fichier introuvable : {input_pdf}")
        raise typer.Exit(1)

    with console.status("[cyan]Analyse de la structure…"):
        extractor = PDFExtractor(input_pdf)
        structure = extractor.extract()
        extractor.close()

    console.print(Panel.fit(
        f"[bold]{structure.title}[/bold]\n"
        f"Pages : {structure.page_count}\n"
        f"Corps de texte : {structure.dominant_font_size}pt\n"
        f"Tailles de titres : {structure.title_font_sizes}\n"
        f"Multi-colonnes : {'oui' if structure.is_multicolumn else 'non'}\n"
        f"Notes de bas de page : {'oui' if structure.has_footnotes else 'non'}\n"
        f"En-têtes/pieds de page : {'oui' if structure.has_headers_footers else 'non'}",
        title="Structure détectée",
    ))

    table = Table(title="Pages de début de chapitre")
    table.add_column("N°", style="bold")
    table.add_column("Page", style="cyan")
    table.add_column("Titre")

    for i, page_num in enumerate(structure.chapter_pages[:30]):
        page = structure.pages[page_num] if page_num < len(structure.pages) else None
        title_text = ""
        if page:
            titles = [b.full_text for b in page.blocks if b.block_type in ("title", "subtitle")]
            title_text = " / ".join(titles[:2])
        table.add_row(str(i + 1), str(page_num + 1), title_text[:60])

    console.print(table)

    # Pages scannées
    scanned = [p.page_num + 1 for p in structure.pages if p.is_scanned]
    if scanned:
        console.print(f"[yellow]Pages scannées (sans texte extractible) :[/yellow] {scanned[:20]}")


# ─────────────────────────────────────────────
# Utilitaires
# ─────────────────────────────────────────────

def _load_config(config_path: Path | None) -> dict:
    """Charge la configuration YAML ou retourne les valeurs par défaut."""
    defaults = {
        "langue": "français",
        "genre": "roman",
        "niveau": "standard",
        "modele": "claude-opus-4-6",
        "taille_chunk_tokens": 2000,
        "chevauchement_tokens": 200,
        "max_requetes_paralleles": 3,
        "noms_propres": [],
        "analyses": {
            "orthographe_grammaire": True,
            "typographie": True,
            "style_lisibilite": True,
            "homogeneisation": True,
            "structure_narrative": True,
            "maquette": True,
        },
    }

    if config_path is None:
        # Cherche un config.yaml dans le répertoire courant
        local = Path("config.yaml")
        if local.exists():
            config_path = local

    if config_path and config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        # Fusion : les valeurs utilisateur écrasent les défauts
        _deep_merge(defaults, user_config)

    return defaults


def _deep_merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _print_summary(issues: list, stats, output: Path, rapport: Path):
    """Affiche le résumé des résultats."""
    from collections import Counter
    by_cat = Counter(i.category for i in issues)
    by_sev = Counter(i.severity for i in issues)

    table = Table(title="Résumé des remarques éditoriales", show_lines=False)
    table.add_column("Catégorie", style="bold")
    table.add_column("Fautes", style="red", justify="right")
    table.add_column("Avertissements", style="yellow", justify="right")
    table.add_column("Suggestions", style="dim", justify="right")
    table.add_column("Total", style="bold", justify="right")

    cat_labels = {
        "orthographe": "Orthographe",
        "typographie": "Typographie",
        "style": "Style",
        "homogeneisation": "Cohérence",
        "structure": "Structure",
        "maquette": "Maquette",
    }

    for cat, label in cat_labels.items():
        cat_issues = [i for i in issues if i.category == cat]
        if not cat_issues:
            continue
        errors = sum(1 for i in cat_issues if i.severity == "error")
        warnings = sum(1 for i in cat_issues if i.severity == "warning")
        suggestions = sum(1 for i in cat_issues if i.severity == "suggestion")
        table.add_row(label,
                      str(errors) if errors else "—",
                      str(warnings) if warnings else "—",
                      str(suggestions) if suggestions else "—",
                      str(len(cat_issues)))

    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        f"[red]{by_sev.get('error', 0)}[/red]",
        f"[yellow]{by_sev.get('warning', 0)}[/yellow]",
        f"{by_sev.get('suggestion', 0)}",
        f"[bold]{len(issues)}[/bold]",
    )

    console.print(table)
    console.print()
    console.print(f"  [green]✓[/green] PDF annoté   → [cyan]{output}[/cyan]")
    console.print(f"  [green]✓[/green] Rapport HTML → [cyan]{rapport}[/cyan]")
    console.print()
    console.print(
        f"  Annotations : [bold]{stats.placed}[/bold] positionnées sur le texte, "
        f"[bold]{stats.fallback}[/bold] en marge"
    )


# ─────────────────────────────────────────────
# Point d'entrée
# ─────────────────────────────────────────────

if __name__ == "__main__":
    app()
