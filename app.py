"""
Interface web de l'outil d'édition professionnelle.
Lance avec : python app.py
Puis ouvre http://localhost:8000
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import yaml
from fastapi import FastAPI, File, Form, UploadFile, Request, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.extractor import PDFExtractor
from src.analyzer import EditorialAnalyzer
from src.annotator import PDFAnnotator
from src.reporter import HTMLReporter

logger = logging.getLogger(__name__)

app = FastAPI(title="Outil d'édition professionnelle")

# Dossier temporaire pour les jobs
JOBS_DIR = Path(tempfile.gettempdir()) / "editrice_jobs"
JOBS_DIR.mkdir(exist_ok=True)

# État des jobs en mémoire
JOBS: dict[str, dict] = {}


# ─────────────────────────────────────────────
# Page d'accueil
# ─────────────────────────────────────────────

HOME_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Outil d'édition professionnelle</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: Georgia, serif;
    background: #f0ede8;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 2rem;
  }
  .card {
    background: white;
    border-radius: 12px;
    padding: 3rem;
    max-width: 620px;
    width: 100%;
    box-shadow: 0 4px 32px rgba(0,0,0,0.08);
  }
  h1 {
    font-size: 1.6rem;
    color: #1a1a2e;
    margin-bottom: 0.4rem;
  }
  .subtitle {
    color: #888;
    font-size: 0.9rem;
    font-family: sans-serif;
    margin-bottom: 2rem;
  }
  .upload-zone {
    border: 2px dashed #ccc;
    border-radius: 8px;
    padding: 2.5rem;
    text-align: center;
    cursor: pointer;
    transition: all 0.2s;
    margin-bottom: 1.5rem;
    position: relative;
  }
  .upload-zone:hover, .upload-zone.drag-over {
    border-color: #e06000;
    background: #fff8f0;
  }
  .upload-zone input[type=file] {
    position: absolute;
    inset: 0;
    opacity: 0;
    cursor: pointer;
    width: 100%;
    height: 100%;
  }
  .upload-icon { font-size: 2.5rem; margin-bottom: 0.5rem; }
  .upload-label { font-family: sans-serif; color: #555; font-size: 0.95rem; }
  .upload-hint { font-family: sans-serif; color: #aaa; font-size: 0.8rem; margin-top: 0.3rem; }
  .file-selected { color: #e06000; font-weight: bold; margin-top: 0.5rem; font-family: sans-serif; font-size: 0.9rem; }

  .options { margin-bottom: 1.5rem; }
  .options label {
    display: block;
    font-family: sans-serif;
    font-size: 0.85rem;
    color: #444;
    margin-bottom: 0.3rem;
    margin-top: 0.9rem;
  }
  .options select, .options input[type=text], .options input[type=password] {
    width: 100%;
    padding: 0.5rem 0.7rem;
    border: 1px solid #ddd;
    border-radius: 6px;
    font-size: 0.9rem;
    font-family: sans-serif;
    outline: none;
    transition: border-color 0.15s;
  }
  .options select:focus, .options input:focus { border-color: #e06000; }

  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }

  button[type=submit] {
    width: 100%;
    padding: 0.9rem;
    background: #1a1a2e;
    color: white;
    border: none;
    border-radius: 8px;
    font-size: 1rem;
    font-family: sans-serif;
    cursor: pointer;
    transition: background 0.2s;
  }
  button[type=submit]:hover { background: #e06000; }
  button[type=submit]:disabled { background: #aaa; cursor: not-allowed; }

  .axes {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin-top: 0.5rem;
  }
  .axes label {
    display: flex;
    align-items: center;
    gap: 0.3rem;
    font-family: sans-serif;
    font-size: 0.8rem;
    background: #f5f5f5;
    border-radius: 20px;
    padding: 0.25rem 0.7rem;
    cursor: pointer;
    margin: 0;
    color: #333;
  }
  .axes label:hover { background: #eee; }
  .axes input[type=checkbox] { accent-color: #e06000; }

  /* Barre de progression */
  #progress-section { display: none; margin-top: 1.5rem; }
  .progress-bar-bg {
    background: #eee;
    border-radius: 8px;
    height: 10px;
    overflow: hidden;
    margin-top: 0.5rem;
  }
  .progress-bar-fill {
    height: 100%;
    background: linear-gradient(90deg, #1a1a2e, #e06000);
    border-radius: 8px;
    width: 0%;
    transition: width 0.5s;
  }
  .progress-status {
    font-family: sans-serif;
    font-size: 0.85rem;
    color: #555;
    margin-top: 0.4rem;
  }
  .spinner {
    display: inline-block;
    width: 12px; height: 12px;
    border: 2px solid #ddd;
    border-top-color: #e06000;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    margin-right: 0.4rem;
    vertical-align: middle;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Résultats */
  #results { display: none; margin-top: 1.5rem; }
  .result-btn {
    display: block;
    width: 100%;
    padding: 0.8rem 1rem;
    margin-bottom: 0.7rem;
    border-radius: 8px;
    text-align: center;
    font-family: sans-serif;
    font-size: 0.95rem;
    font-weight: bold;
    text-decoration: none;
    transition: opacity 0.2s;
  }
  .result-btn:hover { opacity: 0.85; }
  .btn-pdf  { background: #1a1a2e; color: white; }
  .btn-html { background: #e06000; color: white; }
  .stats-row {
    display: flex;
    gap: 1rem;
    font-family: sans-serif;
    font-size: 0.82rem;
    color: #888;
    margin-top: 0.5rem;
    justify-content: center;
  }
  .stat-item strong { color: #333; }

  #error-msg {
    display: none;
    background: #fff0f0;
    border: 1px solid #ffcccc;
    color: #cc0000;
    padding: 0.8rem 1rem;
    border-radius: 6px;
    font-family: sans-serif;
    font-size: 0.88rem;
    margin-top: 1rem;
  }

  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin-top: 0.8rem;
    margin-bottom: 1.5rem;
    justify-content: center;
  }
  .legend-item {
    display: flex;
    align-items: center;
    gap: 0.3rem;
    font-family: sans-serif;
    font-size: 0.75rem;
    color: #555;
  }
  .dot {
    width: 10px; height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
  }
</style>
</head>
<body>
<div class="card">
  <h1>Outil d'édition professionnelle</h1>
  <p class="subtitle">Analyse un manuscrit PDF et injecte des annotations éditoriales Adobe Acrobat</p>

  <div class="legend">
    <div class="legend-item"><div class="dot" style="background:#e01010"></div> Orthographe</div>
    <div class="legend-item"><div class="dot" style="background:#e06000"></div> Typographie</div>
    <div class="legend-item"><div class="dot" style="background:#b09000"></div> Style</div>
    <div class="legend-item"><div class="dot" style="background:#1464d8"></div> Cohérence</div>
    <div class="legend-item"><div class="dot" style="background:#7010c0"></div> Structure</div>
    <div class="legend-item"><div class="dot" style="background:#107820"></div> Maquette</div>
  </div>

  <form id="upload-form" enctype="multipart/form-data">

    <div class="upload-zone" id="drop-zone">
      <input type="file" name="pdf" id="pdf-input" accept=".pdf" required>
      <div class="upload-icon">📄</div>
      <div class="upload-label">Glissez votre PDF ici ou cliquez pour parcourir</div>
      <div class="upload-hint">Manuscrit ou livre maquetté · PDF uniquement</div>
      <div class="file-selected" id="file-name"></div>
    </div>

    <div class="options">
      <div class="row">
        <div>
          <label>Genre de l'ouvrage</label>
          <select name="genre">
            <option value="roman">Roman</option>
            <option value="essai">Essai</option>
            <option value="documentaire">Documentaire</option>
            <option value="jeunesse">Jeunesse</option>
            <option value="technique">Technique</option>
          </select>
        </div>
        <div>
          <label>Niveau d'intervention</label>
          <select name="niveau">
            <option value="leger">Léger (fautes graves)</option>
            <option value="standard" selected>Standard (équilibré)</option>
            <option value="approfondi">Approfondi (complet)</option>
          </select>
        </div>
      </div>

      <div class="row">
        <div>
          <label>Temps narratif</label>
          <select name="temps_narratif">
            <option value="passe" selected>Passé</option>
            <option value="present">Présent</option>
            <option value="mixte">Mixte</option>
          </select>
        </div>
        <div>
          <label>Modèle</label>
          <select name="modele">
            <option value="claude-haiku-4-5-20251001" selected>Haiku 4.5 (économique)</option>
            <option value="claude-sonnet-4-6">Sonnet 4.6 (équilibré)</option>
            <option value="claude-opus-4-6">Opus 4.6 (précis)</option>
          </select>
        </div>
      </div>

      <label>Clé API Anthropic</label>
      <input type="password" name="api_key" placeholder="sk-ant-... (ou via variable ANTHROPIC_API_KEY)" autocomplete="off">

      <label>Axes d'analyse</label>
      <div class="axes">
        <label><input type="checkbox" name="axes" value="orthographe_grammaire" checked> 🔴 Orthographe</label>
        <label><input type="checkbox" name="axes" value="typographie" checked> 🟠 Typographie</label>
        <label><input type="checkbox" name="axes" value="style_lisibilite" checked> 🟡 Style</label>
        <label><input type="checkbox" name="axes" value="homogeneisation" checked> 🔵 Cohérence</label>
        <label><input type="checkbox" name="axes" value="structure_narrative" checked> 🟣 Structure</label>
        <label><input type="checkbox" name="axes" value="maquette" checked> 🟢 Maquette</label>
      </div>
    </div>

    <button type="submit" id="submit-btn">Analyser le manuscrit</button>
  </form>

  <div id="progress-section">
    <div class="progress-status" id="progress-status">
      <span class="spinner"></span> Initialisation…
    </div>
    <div class="progress-bar-bg">
      <div class="progress-bar-fill" id="progress-bar"></div>
    </div>
  </div>

  <div id="results">
    <a id="pdf-link" href="#" class="result-btn btn-pdf" download>⬇ Télécharger le PDF annoté</a>
    <a id="html-link" href="#" class="result-btn btn-html" target="_blank">📊 Ouvrir le rapport HTML</a>
    <div class="stats-row" id="stats-row"></div>
  </div>

  <div id="error-msg"></div>
</div>

<script>
const form = document.getElementById('upload-form');
const dropZone = document.getElementById('drop-zone');
const pdfInput = document.getElementById('pdf-input');
const fileName = document.getElementById('file-name');
const submitBtn = document.getElementById('submit-btn');
const progressSection = document.getElementById('progress-section');
const progressStatus = document.getElementById('progress-status');
const progressBar = document.getElementById('progress-bar');
const resultsDiv = document.getElementById('results');
const errorMsg = document.getElementById('error-msg');

// Drag & drop
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  if (e.dataTransfer.files[0]) {
    pdfInput.files = e.dataTransfer.files;
    fileName.textContent = e.dataTransfer.files[0].name;
  }
});
pdfInput.addEventListener('change', () => {
  fileName.textContent = pdfInput.files[0]?.name || '';
});

// Submit
form.addEventListener('submit', async e => {
  e.preventDefault();
  errorMsg.style.display = 'none';
  resultsDiv.style.display = 'none';

  const data = new FormData(form);

  // Axes cochés
  const checked = [...document.querySelectorAll('input[name=axes]:checked')].map(el => el.value);
  data.delete('axes');
  checked.forEach(v => data.append('axes', v));

  submitBtn.disabled = true;
  submitBtn.textContent = 'Analyse en cours…';
  progressSection.style.display = 'block';
  progressBar.style.width = '5%';

  try {
    // Lancer le job
    const resp = await fetch('/analyse', { method: 'POST', body: data });
    const json = await resp.json();
    if (!resp.ok) { showError(json.detail || 'Erreur serveur'); return; }

    const jobId = json.job_id;
    await pollJob(jobId);

  } catch(err) {
    showError('Erreur réseau : ' + err.message);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Analyser le manuscrit';
  }
});

async function pollJob(jobId) {
  const steps = {
    'extraction': { label: 'Extraction du PDF…',       pct: 15 },
    'rules':      { label: 'Règles typographiques…',   pct: 30 },
    'grammar':    { label: 'Analyse grammaticale…',    pct: 50 },
    'style':      { label: 'Analyse de style…',        pct: 65 },
    'coherence':  { label: 'Cohérence et structure…',  pct: 80 },
    'annotation': { label: 'Injection des annotations…', pct: 92 },
    'report':     { label: 'Génération du rapport…',   pct: 97 },
    'done':       { label: 'Terminé !',                pct: 100 },
  };

  while (true) {
    await new Promise(r => setTimeout(r, 1500));
    const r = await fetch(`/job/${jobId}`);
    const job = await r.json();

    const step = steps[job.step] || { label: job.step, pct: 10 };
    progressStatus.innerHTML = `<span class="spinner"></span> ${step.label}`;
    progressBar.style.width = step.pct + '%';

    if (job.status === 'done') {
      progressStatus.innerHTML = '✅ Analyse terminée';
      showResults(jobId, job.stats);
      break;
    }
    if (job.status === 'error') {
      showError(job.error);
      break;
    }
  }
}

function showResults(jobId, stats) {
  progressSection.style.display = 'none';
  resultsDiv.style.display = 'block';
  document.getElementById('pdf-link').href = `/download/${jobId}/pdf`;
  document.getElementById('html-link').href = `/download/${jobId}/html`;

  if (stats) {
    const s = document.getElementById('stats-row');
    s.innerHTML =
      `<div><strong>${stats.total}</strong> remarques</div>` +
      `<div>🔴 ${stats.orthographe||0} ortho</div>` +
      `<div>🟠 ${stats.typographie||0} typo</div>` +
      `<div>🟡 ${stats.style||0} style</div>` +
      `<div>🔵 ${stats.homogeneisation||0} cohér.</div>`;
  }
}

function showError(msg) {
  progressSection.style.display = 'none';
  errorMsg.style.display = 'block';
  errorMsg.textContent = '❌ ' + msg;
}
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────
# Routes API
# ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home():
    return HOME_HTML


@app.post("/analyse")
async def start_analyse(
    background_tasks: BackgroundTasks,
    pdf: UploadFile = File(...),
    genre: str = Form("roman"),
    niveau: str = Form("standard"),
    temps_narratif: str = Form("passe"),
    modele: str = Form("claude-haiku-4-5-20251001"),
    api_key: str = Form(""),
    axes: list[str] = Form(default=[]),
):
    # Vérifier clé API
    resolved_key = api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
    if not resolved_key:
        return JSONResponse(
            status_code=400,
            content={"detail": "Clé API Anthropic manquante. Entrez-la dans le formulaire ou définissez ANTHROPIC_API_KEY."}
        )

    if not pdf.filename.lower().endswith(".pdf"):
        return JSONResponse(status_code=400, content={"detail": "Le fichier doit être un PDF."})

    # Créer le dossier du job
    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir()

    # Sauvegarder le PDF uploadé
    input_path = job_dir / "input.pdf"
    content = await pdf.read()
    input_path.write_bytes(content)

    # Config
    analyses = {
        "orthographe_grammaire": "orthographe_grammaire" in axes,
        "typographie": "typographie" in axes,
        "style_lisibilite": "style_lisibilite" in axes,
        "homogeneisation": "homogeneisation" in axes,
        "structure_narrative": "structure_narrative" in axes,
        "maquette": "maquette" in axes,
    }
    # Si aucune case cochée → tout activer
    if not any(analyses.values()):
        analyses = {k: True for k in analyses}

    config = {
        "genre": genre,
        "niveau": niveau,
        "temps_narratif": temps_narratif,
        "modele": modele,
        "analyses": analyses,
        "taille_chunk_tokens": 2000,
        "chevauchement_tokens": 200,
        "max_requetes_paralleles": 3,
        "noms_propres": [],
    }

    # Enregistrer le job
    JOBS[job_id] = {"status": "running", "step": "extraction", "error": None, "stats": None}

    # Lancer en arrière-plan
    background_tasks.add_task(_run_analysis, job_id, job_dir, input_path, config, resolved_key)

    return {"job_id": job_id}


@app.get("/job/{job_id}")
async def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"detail": "Job introuvable"})
    return job


@app.get("/download/{job_id}/{file_type}")
async def download(job_id: str, file_type: str):
    job_dir = JOBS_DIR / job_id
    if file_type == "pdf":
        path = job_dir / "output_annoté.pdf"
        media = "application/pdf"
        filename = "manuscrit_annoté.pdf"
    elif file_type == "html":
        path = job_dir / "rapport.html"
        media = "text/html"
        filename = "rapport_éditorial.html"
    else:
        return JSONResponse(status_code=400, content={"detail": "Type inconnu"})

    if not path.exists():
        return JSONResponse(status_code=404, content={"detail": "Fichier non disponible"})

    return FileResponse(str(path), media_type=media, filename=filename)


# ─────────────────────────────────────────────
# Traitement en arrière-plan
# ─────────────────────────────────────────────

async def _run_analysis(job_id: str, job_dir: Path, input_path: Path, config: dict, api_key: str):
    job = JOBS[job_id]
    try:
        output_pdf = job_dir / "output_annoté.pdf"
        output_html = job_dir / "rapport.html"

        # Extraction
        job["step"] = "extraction"
        extractor = PDFExtractor(input_path)
        structure = await asyncio.get_event_loop().run_in_executor(None, extractor.extract)
        chunks = extractor.get_chunks(structure)
        extractor.close()

        # Analyse
        job["step"] = "rules"
        analyzer = EditorialAnalyzer(config, api_key=api_key)

        job["step"] = "grammar"
        issues = await asyncio.get_event_loop().run_in_executor(
            None, lambda: analyzer.analyze(structure, chunks)
        )

        # Annotations
        job["step"] = "annotation"
        def _annotate():
            annotator = PDFAnnotator(input_path, output_pdf)
            stats = annotator.annotate(issues)
            annotator.add_legend_page()
            annotator.save()
            annotator.close()
            return stats

        annot_stats = await asyncio.get_event_loop().run_in_executor(None, _annotate)

        # Rapport HTML
        job["step"] = "report"
        def _report():
            reporter = HTMLReporter(structure, issues)
            reporter.generate(output_html)

        await asyncio.get_event_loop().run_in_executor(None, _report)

        # Statistiques
        from collections import Counter
        by_cat = Counter(i.category for i in issues)
        job["stats"] = {
            "total": len(issues),
            "placed": annot_stats.placed,
            "fallback": annot_stats.fallback,
            **{cat: count for cat, count in by_cat.items()},
        }

        job["status"] = "done"
        job["step"] = "done"

    except Exception as e:
        logger.exception(f"Job {job_id} échoué")
        job["status"] = "error"
        job["error"] = str(e)


# ─────────────────────────────────────────────
# Point d'entrée
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  Outil d'édition professionnelle")
    print("  → http://localhost:8000")
    print("=" * 50)
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
