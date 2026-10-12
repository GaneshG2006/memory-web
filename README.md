# Memory Web

Memory Web is a local conversation-graph prototype for exploring discussions as connected messages and topic nodes instead of a single linear transcript. Users can inspect nodes and continue or branch a conversation from an existing message.

## Architecture

- **Frontend:** React, TypeScript, Vite, and React Flow (`@xyflow/react`).
- **Backend:** FastAPI with SQLAlchemy models and Alembic migrations.
- **Persistence:** PostgreSQL stores conversations, message/topic nodes, and graph edges.
- **Tests:** Python API tests use an isolated SQLite database; frontend graph-layout tests run with Node's test runner.

The frontend talks to the local API at `http://127.0.0.1:8000`. The graph layout is deterministic for reordered graph data, separates branches into distinct lanes, avoids collisions for new nodes, and preserves saved node positions in browser storage.

## Implemented

- Interactive graph with message and topic nodes, selectable node details, and draggable node positions.
- Branch and Continue follow-up flows, persisted as graph nodes and edges.
- Idempotently seeded demo conversation data.
- Health and graph API endpoints, plus follow-up validation and persistence.
- Deterministic graph placement, branch lanes, collision avoidance, and frontend layout tests.
- PostgreSQL schema migrations and automated backend API tests.

**Assistant responses are placeholders.** Follow-up creation currently returns a demo response anchored to the selected parent content; real LLM integration and context-aware response generation are planned, not implemented.

## Local development on Windows

Prerequisites: Git, Python 3.11 or newer, Node.js with npm, and Docker Desktop with Docker Compose.

Run commands in PowerShell from the repository root unless otherwise noted.

### 1. Start PostgreSQL

```powershell
docker compose up -d
```

The Compose service exposes PostgreSQL on port `5432` and creates the local `memory_web` database. Its credentials are for local development only.

### 2. Configure and start the backend

Create a virtual environment, install dependencies, and copy the example environment file:

```powershell
Set-Location .\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
Set-Location ..
```

Review `backend\.env` and set `DATABASE_URL` to match the local PostgreSQL service. Apply migrations from the repository root:

```powershell
.\backend\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
```

Start the API in a separate PowerShell window:

```powershell
Set-Location .\backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

The API listens at `http://127.0.0.1:8000`.

### 3. Start the frontend

In another PowerShell window:

```powershell
Set-Location .\frontend
npm.cmd install
npm.cmd run dev
```

Open the local URL printed by Vite, normally `http://localhost:5173`.

## API

- `GET /health` — API health status.
- `GET /api/conversations/{conversation_id}/graph` — graph nodes and edges for a conversation; the UI loads the seeded `demo` conversation.
- `POST /api/follow-ups` — create a follow-up from a parent node. JSON body: `conversation_id`, `parent_node_id`, `mode` (`continue` or `branch`), and `prompt`. Returns the created user node, placeholder assistant node, and connecting edges.
- `GET /docs` — interactive FastAPI documentation when the backend is running.

## Tests and build

From the repository root, run backend API tests:

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend\tests -q
```

Run frontend layout tests and the production build:

```powershell
Set-Location .\frontend
npm.cmd test
npm.cmd run build
```

The test TypeScript project can also be checked directly:

```powershell
npx.cmd tsc --project tsconfig.test.json
```

## Planned

- Integrate a real LLM provider and assemble context from conversation ancestry.
- Extract topics automatically rather than relying on seeded/demo topic data.
- Add user accounts and authentication.
- Explore a browser extension and deployment.

## Security and project status

This is a local prototype, not a production-ready or deployed service. Authentication is not implemented. Do not commit `.env` files, credentials, or API keys. The Compose database credentials are development-only; use secure secrets management and restrict access before any hosted deployment. No API keys are currently needed because LLM integration is not implemented.
