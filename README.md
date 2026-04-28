# summarisegit

`summarisegit` is a repo-to-Claude context compiler and code intelligence workstation for unfamiliar repositories. It does not treat code as raw text blobs. Instead, it ingests repositories structurally, extracts symbols and imports, builds dependency graphs, and produces a **Repo Intelligence Pack** that is useful for onboarding, impact analysis, interviews, PR reviews, and AI context compression.

## Why It Matters

Most repo tools stop at "summarize this codebase." `summarisegit` goes further:

- maps repo structure into file, folder, and function-level views
- explains what important files and functions actually do
- estimates what breaks when a file or function changes
- suggests tests to run before you ship changes
- detects code smells, risky dependencies, and architecture bottlenecks
- exports compact Claude-ready context so you do not waste tokens pasting an entire repo

## Core Product Modes

### Repo Intelligence Pack

One click export that generates:

- `repo_context.md`
- `architecture.md`
- `function_map.md`
- `improvement_plan.md`
- `dependency_graph.json`

### Ask Repo

Ask questions such as:

- `What happens when /api/analyze is called?`
- `Where is authentication handled?`
- `Which file should I change to add Java support?`
- `What functions are risky?`
- `Explain this repo for an interview.`

### Impact Analysis

Given a file or function, the engine returns:

- direct dependents
- indirect dependents
- affected files
- suggested test files
- a heuristic risk score

### Code Flow Simulation

Trace how execution moves through the repository from a starting symbol, with a Mermaid flow preview and step-by-step explanation.

## Feature Set

- branch-aware repo ingestion from local path, GitHub URL, or zip upload
- language-aware filtering and ignore rules for Python and JavaScript/TypeScript
- function-level and class-level chunking for retrieval
- file dependency graph and symbol call graph
- multi-level summaries across repo, folder, file, and function scopes
- hybrid retrieval with TF-IDF plus keyword fallback and graph context
- function explanation cards with inputs, outputs, callees, and risk labels
- architecture narrative for engineers and a separate "Explain Like I'm New" mode
- PR diff review mode for touched files and likely risk areas
- improvement engine for large files, god functions, dead code candidates, poor naming, missing tests, risky dependencies, security smells, and performance smells
- compression modes for `tiny`, `medium`, `deep`, `claude`, and `interview`
- interactive frontend with repo map, graph canvas, function cards, details panel, and export actions

## Tech Stack

- Backend: FastAPI
- Parsing: Python `ast` plus JavaScript/TypeScript structural parsing
- Retrieval: TF-IDF via scikit-learn, keyword fallback
- Graph model: in-memory adjacency lists for file and symbol dependencies
- Frontend: HTML, CSS, vanilla JS, D3.js

## Architecture

```text
User Input
   ↓
Repository Materializer (local path / GitHub clone / zip upload)
   ↓
Language-aware filtering
   ↓
Parser + Symbol Extractor
   ↓
Dependency Graph Builder
   ↓
Hybrid Retrieval (TF-IDF + keyword + graph context)
   ↓
Reasoning Layer (impact, flow, architecture, improvements, diff review)
   ↓
Repo Intelligence Pack + UI Workspace
```

## API Surface

- `POST /api/analyze`
- `GET /api/reports/{report_id}`
- `GET /api/reports/{report_id}/graph?kind=file|symbol`
- `GET /api/reports/{report_id}/impact?target=...`
- `GET /api/reports/{report_id}/flow?target=...`
- `GET /api/reports/{report_id}/search?q=...`
- `POST /api/reports/{report_id}/ask`
- `POST /api/reports/{report_id}/export`
- `GET /api/reports/{report_id}/explain?mode=architecture|newcomer|file|symbol|refactor|dead-code|improvements`
- `GET /api/reports/{report_id}/source?path=...`
- `POST /api/reports/{report_id}/review-diff`

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8010
```

Then open [http://127.0.0.1:8010](http://127.0.0.1:8010).

## Resume Version

**summarisegit | FastAPI, AST, Dependency Graphs, TF-IDF, D3.js**

- Built a repo intelligence platform that parses repositories into function-level chunks and dependency graphs for architecture discovery and codebase navigation
- Implemented impact analysis to detect direct and transitive change blast radius across files, symbols, and tests with heuristic risk scoring
- Developed a Repo Intelligence Pack exporter that generates Claude-ready markdown context, architecture summaries, function maps, and dependency graph artifacts
- Added hybrid retrieval, code flow tracing, newcomer-friendly explanations, and diff review workflows for faster onboarding and safer repo changes
