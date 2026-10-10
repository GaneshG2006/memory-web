# Memory Web 

A standalone conversation graph prototype using React + TypeScript + Vite, React Flow, FastAPI, and PostgreSQL.

## Prerequisites
- Node.js 20+
- Python 3.11+
- Docker Desktop (recommended for PostgreSQL)

## 1. Start PostgreSQL
From the project root:

```bash
docker compose up -d db
```

The local database is exposed at `localhost:5432`.
Database: `memory_web` · User: `memory_user` · Password: `memory_dev_password`

These credentials are for local development only. Change them before any deployment.

## 2. Start the backend
Open a terminal:

```bash
cd backend
python -m venv .venv
```

Windows PowerShell:
```powershell
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:
```bash
source .venv/bin/activate
```

Then:
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API docs: http://127.0.0.1:8000/docs
Health endpoint: http://127.0.0.1:8000/health

The starter API uses an in-memory demo graph so you can test the first vertical slice before database persistence is implemented.

## 3. Start the frontend
Open a second terminal from the project root:

```bash
cd frontend
npm install
npm run dev
```

Open the URL Vite prints, normally http://localhost:5173.

## What works in this starter
- Interactive graph with message and topic nodes
- Select a node to inspect its content
- Choose Continue or Branch
- Send a follow-up to the local API
- API stores demo messages in memory until the server restarts

## What is intentionally not implemented yet
- Real LLM provider integration
- Persistent database models and migrations
- Login, accounts, cloud deployment
- Automatic topic extraction
- Browser extension

Build these incrementally after verifying the starter works. Never put an LLM API key in frontend code.
