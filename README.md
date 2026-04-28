# summarisegit

`summarisegit` is a live codebase intelligence engine that turns repositories into structured graphs, impact reports, architecture summaries, and searchable code knowledge.

It is designed to answer questions like:

- Where is this function used?
- What breaks if I change this file?
- Which tests should I run after a change?
- How is this repository structured?

## What It Does

- Ingests a local repository, uploaded zip, or GitHub repo URL
- Parses Python and JavaScript/TypeScript files
- Extracts functions, classes, imports, and function calls
- Builds:
  - file dependency graph
  - function call graph
- Generates:
  - repository summary
  - impact analysis
  - hybrid search results
  - architecture and refactor explanations

## Recruiter-Friendly Highlights

- AST-based Python parsing for symbol-level understanding
- JavaScript/TypeScript structural extraction for multi-language coverage
- Graph-backed impact analysis with direct and transitive dependency traversal
- Hybrid retrieval that combines keyword search, TF-IDF similarity, and graph context
- Interactive frontend with graph visualization and code/source inspection

## Tech Stack

- Backend: FastAPI
- Parsing: Python `ast` + JS/TS regex extraction
- Search: scikit-learn TF-IDF
- Frontend: Vanilla JS + D3.js
- Storage: in-memory report cache for the MVP

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## API Overview

- `POST /api/analyze`
- `GET /api/reports/{report_id}`
- `GET /api/reports/{report_id}/graph?kind=file|symbol`
- `GET /api/reports/{report_id}/impact?target=...`
- `GET /api/reports/{report_id}/search?q=...`
- `GET /api/reports/{report_id}/explain?mode=architecture|file|symbol|refactor|dead-code`
- `GET /api/reports/{report_id}/source?path=...`

## Resume-Ready Summary

**Codebase Intelligence Engine | FastAPI, AST, Graphs, TF-IDF, D3.js**

- Built a code intelligence platform that parses repositories into file- and function-level dependency graphs for architecture understanding and impact analysis
- Implemented graph traversal to identify direct and transitive change blast radius, affected files, and test recommendations
- Developed hybrid code search by combining keyword overlap, TF-IDF similarity, and graph context for high-precision repository discovery
- Added explanation workflows for architecture summaries, file importance, dead-code detection, and refactor suggestions
