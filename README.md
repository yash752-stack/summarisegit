# summarisegit

`summarisegit` is an AI-powered code understanding system for unfamiliar repositories. Instead of just summarizing files, it ingests code structurally, builds dependency graphs, chunks code at the function/class level, and answers developer-focused questions like:

- Where is this function used?
- What breaks if I change this file?
- Which tests should I run?
- What happens when a user logs in?
- Which files are the architectural entry points?

## What Changed From A Basic Repo Summarizer

This project is intentionally positioned as a **developer copilot for unfamiliar repos**, not a generic GitHub summary tool.

### Upgraded capabilities

- Branch-aware repo ingestion from local path, GitHub URL, or uploaded zip
- Language-aware filtering for Python and JavaScript/TypeScript
- Function-level and class-level chunking for retrieval
- File dependency graph and function call graph
- Multi-level summaries across repo, folder, file, and function layers
- Hybrid retrieval with TF-IDF plus graph context, with keyword fallback when embeddings are unavailable
- Impact analysis with direct dependents, indirect dependents, affected files, risk score, and suggested tests
- "Explain like I'm new" architecture mode
- Code flow tracing for symbol-level execution walkthroughs
- PR review mode for diff-based risk spotting
- Lightweight in-memory caching for repeated analyses in the same session

## Tech Stack

- Backend: FastAPI
- Parsing: Python `ast` + JS/TS structural extraction
- Retrieval: TF-IDF via scikit-learn, keyword fallback
- Graph model: in-memory adjacency lists for file and symbol dependencies
- Frontend: HTML, CSS, vanilla JS, D3.js

## Architecture

```text
Frontend
   ↓
Ingestion Layer (local path / GitHub clone / zip upload)
   ↓
Language-aware filtering
   ↓
Chunking Engine (function/class/file)
   ↓
Dependency Graph Builder
   ↓
Hybrid Retrieval (TF-IDF + keyword + graph context)
   ↓
Reasoning Layer (impact, flow, architecture, refactor, diff review)
```

## API Surface

- `POST /api/analyze`
  - inputs: local path, repo URL, branch, extension filters, exclude dirs, or zip upload
- `GET /api/reports/{report_id}`
- `GET /api/reports/{report_id}/graph?kind=file|symbol`
- `GET /api/reports/{report_id}/impact?target=...`
- `GET /api/reports/{report_id}/flow?target=...`
- `GET /api/reports/{report_id}/search?q=...`
- `GET /api/reports/{report_id}/explain?mode=architecture|newcomer|file|symbol|refactor|dead-code`
- `GET /api/reports/{report_id}/source?path=...`
- `POST /api/reports/{report_id}/review-diff`

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Resume Version

**summarisegit | FastAPI, AST, Dependency Graphs, TF-IDF, D3.js**

- Built a code understanding system that ingests repositories into function-level chunks and graph-based dependency structures for architecture discovery and impact analysis
- Implemented branch-aware ingestion, language filtering, and parallel parsing for Python and JavaScript/TypeScript projects
- Developed hybrid retrieval using TF-IDF, keyword search, and graph context to answer structural questions about unfamiliar codebases
- Added code-flow tracing, newcomer-friendly architecture explanations, and diff-based risk review for developer onboarding and change analysis
