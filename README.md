# Memory Web

Memory Web is a local prototype for exploring conversations as connected message and topic nodes rather than as a single transcript. Select a node to inspect it, then continue the conversation or create a branch from that point. The backend stores the graph and conversation messages; a local Ollama model generates assistant replies.

## Architecture

- **Frontend:** React and TypeScript, bundled and served for development with Vite. React Flow (`@xyflow/react`) renders the conversation graph.
- **Backend:** FastAPI exposes the JSON API. SQLAlchemy maps conversations, nodes, and graph edges; Alembic manages PostgreSQL schema migrations.
- **Database:** PostgreSQL stores saved conversations, message/topic nodes, and graph edges.
- **Local AI:** Ollama serves `qwen2.5:7b` over its local HTTP API. The backend uses `httpx` to send non-streaming chat requests; no cloud AI provider or API key is used.
- **Tests:** Backend API tests use an isolated SQLite database and mock Ollama HTTP responses. Frontend graph-layout tests use Node's test runner.

The frontend currently loads the seeded `demo` conversation from the API at `http://127.0.0.1:8000`. Node positions are stored in browser session storage. The graph layout is deterministic for reordered API data, separates branches into lanes, and avoids collisions when positioning new nodes.

## Implemented

- Interactive conversation graph with message and topic nodes, node inspection, and draggable positions.
- Case-insensitive search across message and topic content in the current conversation, with results that focus the matching graph node.
- Continue and Branch follow-ups saved as graph nodes and edges.
- Local assistant response generation through Ollama using Qwen2.5 7B.
- Context assembly from the selected node's ancestry in chronological order. Sibling branches and unrelated conversations are excluded; topic nodes are sent as supporting context. Context is bounded to at most 32 ancestry nodes and 12,000 characters.
- Ollama URL, model, and request timeout configuration through environment variables.
- Explicit API errors for unavailable/timed-out Ollama requests and invalid/empty model responses. Failed generation does not save partial follow-up nodes or edges.
- Health and graph endpoints, request validation, idempotent demo data seeding, and saved graph data using the existing PostgreSQL schema.
- Automated backend API and frontend tests.

## Windows local development

Prerequisites: Git, Python 3.11 or newer, Node.js with npm, Docker Desktop with Docker Compose, and Ollama for Windows.

Run the following commands in PowerShell from the project root unless a step says otherwise.

### 1. Start PostgreSQL

```powershell
docker compose up -d
```

The Compose service makes PostgreSQL available on port `5432` and creates the local `memory_web` database. The Compose credentials are for local development only; do not reuse them in a hosted environment.

### 2. Install and configure the backend

```powershell
Set-Location .\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
Set-Location ..
```

Edit `backend\.env` locally and set `DATABASE_URL` to the PostgreSQL connection string for your environment. Keep `.env` private and do not paste its contents into documentation or commit it. The Ollama settings and their defaults are listed below. Apply the existing migrations from the project root:

```powershell
.\backend\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
```

### 3. Prepare Ollama

Ensure Ollama is running, then download the configured model if it is not already installed:

```powershell
ollama pull qwen2.5:7b
ollama list
```

The API calls Ollama locally at `http://127.0.0.1:11434/api/chat` by default. No API key or cloud account is required.

### 4. Start the backend

In a PowerShell window:

```powershell
Set-Location .\backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

The API listens at `http://127.0.0.1:8000`; interactive API documentation is at `http://127.0.0.1:8000/docs`.

### 5. Start the frontend

In a separate PowerShell window:

```powershell
Set-Location .\frontend
npm.cmd install
npm.cmd run dev
```

Open the Vite URL printed in the terminal, normally `http://localhost:5173`.

## Environment configuration

Set these variables in `backend\.env`. Defaults are used when an Ollama setting is omitted:

| Variable | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama server base URL; the backend appends `/api/chat`. |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Local model name sent to Ollama. |
| `OLLAMA_TIMEOUT` | `120` | Maximum request time in seconds; must be a positive number. |

`DATABASE_URL` must point to the intended PostgreSQL database. Keep actual credentials in the untracked local `.env`; never put passwords, API secrets, or `.env` contents in this README.

## API

- `GET /health` — API health status.
- `GET /api/conversations/{conversation_id}/graph` — graph nodes and edges for a conversation; the current UI requests the seeded `demo` conversation.
- `GET /api/conversations/{conversation_id}/search?q={query}` — case-insensitive substring search of message and topic content in the selected conversation. Returns node metadata and a matching content excerpt; blank queries return no results.
- `POST /api/follow-ups` — create a follow-up from a parent node. JSON body fields: `conversation_id`, `parent_node_id`, `mode` (`continue` or `branch`), and `prompt`. Returns the created user node, Ollama-generated assistant node, and connecting edges.
- `GET /docs` — interactive FastAPI documentation when the backend is running.

## Tests and build

From the project root, run backend API tests:

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend\tests -q
```

Run frontend graph-layout tests and build the production bundle:

```powershell
Set-Location .\frontend
npm.cmd test
npm.cmd run build
```

The frontend test TypeScript project can also be checked directly:

```powershell
npx.cmd tsc --project tsconfig.test.json
```

Backend tests mock Ollama; they do not require the Ollama service or model to be running.

## Limitations and planned improvements

**Current limitations**

- This is a local prototype, not a production-ready or deployed service. Authentication and user accounts are not implemented.
- The UI currently loads the seeded `demo` conversation; it does not provide conversation creation or selection.
- Demo topics are seeded rather than extracted automatically.
- Assistant generation is a synchronous local HTTP request. The UI waits for it to complete; streaming responses are not implemented.
- Context is deliberately bounded, so very long ancestry histories are truncated.
- The Compose database credentials are development-only. The application has not been hardened for public network access or multi-user hosting.

**Planned**

- Automatically extract topics from conversations.
- Add user accounts and authentication.
- Explore a browser extension and deployment.

## Security and project status

Do not commit `.env` files, database credentials, or API keys. No AI API keys are needed for the current local Ollama integration. Before any hosted deployment, replace development database credentials, add authentication and secure secret management, and restrict network access.
