import logging
import math
import os
from secrets import compare_digest
from uuid import uuid4

import httpx
from google import genai
from google.genai import errors, types
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Conversation, Node, Message, TopicNode, GraphEdge
from app.seed import seed_demo_data

logger = logging.getLogger(__name__)

app = FastAPI(title="Memory Web API", version="0.2.0")

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
DEFAULT_GEMINI_TIMEOUT = 60.0
MAX_CONTEXT_NODES = 32
MAX_CONTEXT_CHARS = 12000

basic_auth = HTTPBasic(auto_error=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://memory-web-cvn.pages.dev",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)


class FollowUpRequest(BaseModel):
    conversation_id: str = "demo"
    parent_node_id: str
    mode: str = Field(pattern="^(continue|branch)$")
    prompt: str = Field(min_length=1, max_length=8000)


def require_basic_auth(
    credentials: HTTPBasicCredentials | None = Depends(basic_auth),
) -> str:
    expected_username = os.getenv("MEMORY_WEB_USERNAME")
    expected_password = os.getenv("MEMORY_WEB_PASSWORD")
    if not expected_username or not expected_password:
        raise HTTPException(
            status_code=503,
            detail="API authentication is not configured",
        )

    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="Memory Web"'},
        )

    username_matches = compare_digest(
        credentials.username.encode("utf-8"),
        expected_username.encode("utf-8"),
    )
    password_matches = compare_digest(
        credentials.password.encode("utf-8"),
        expected_password.encode("utf-8"),
    )
    if not (username_matches and password_matches):
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": 'Basic realm="Memory Web"'},
        )
    return credentials.username


def node_to_dict(node: Node) -> dict:
    return {
        "id": node.id,
        "conversation_id": node.conversation_id,
        "parent_id": node.parent_id,
        "branch_id": node.branch_id,
        "role": node.role,
        "content": node.content,
        "kind": node.kind,
    }


def edge_to_dict(edge: GraphEdge) -> dict:
    return {
        "id": edge.id,
        "source": edge.source,
        "target": edge.target,
        "type": edge.type,
    }


def build_follow_up_messages(
    db: Session,
    parent: Node,
    prompt: str,
) -> list[dict[str, str]]:
    ancestry: list[Node] = []
    seen_ids: set[str] = set()
    current: Node | None = parent

    while current is not None and len(ancestry) < MAX_CONTEXT_NODES:
        if current.id in seen_ids or current.conversation_id != parent.conversation_id:
            break
        seen_ids.add(current.id)
        ancestry.append(current)
        current = db.get(Node, current.parent_id) if current.parent_id else None

    context: list[tuple[Node, str]] = []
    remaining_chars = MAX_CONTEXT_CHARS
    for node in ancestry:
        if node.role not in {"user", "assistant"} and node.kind != "topic":
            continue
        if remaining_chars <= 0:
            break
        content = node.content[-remaining_chars:]
        context.append((node, content))
        remaining_chars -= len(content)

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "Continue the conversation helpfully. Topic entries are supporting context, "
                "not prior user or assistant turns."
            ),
        }
    ]
    for node, content in reversed(context):
        if node.kind == "topic" or node.role == "topic":
            messages.append({
                "role": "system",
                "content": f"Supporting topic context: {content}",
            })
        elif node.role in {"user", "assistant"}:
            messages.append({"role": node.role, "content": content})
    messages.append({"role": "user", "content": prompt})
    return messages


async def generate_assistant_response(messages: list[dict[str, str]]) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Gemini API is not configured. Set GEMINI_API_KEY in the backend environment.",
        )

    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
    try:
        timeout = float(os.getenv("GEMINI_TIMEOUT", str(DEFAULT_GEMINI_TIMEOUT)))
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="GEMINI_TIMEOUT must be a number") from exc

    if not model or not math.isfinite(timeout) or timeout <= 0:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_MODEL and GEMINI_TIMEOUT must be valid",
        )

    system_instruction = messages[0]["content"]
    contents = [
        types.Content(
            role="model" if message["role"] == "assistant" else "user",
            parts=[types.Part.from_text(text=message["content"])],
        )
        for message in messages[1:]
    ]

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=int(timeout * 1000)),
    )
    try:
        response = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system_instruction),
        )
    except (httpx.TimeoutException, TimeoutError) as exc:
        logger.warning("Gemini API request timed out")
        raise HTTPException(
            status_code=504,
            detail="Gemini API request timed out. Please try again.",
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("Gemini API transport failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="Gemini API could not complete the request. Please try again.",
        ) from exc
    except errors.APIError as exc:
        logger.warning("Gemini API returned status %s", exc.code)
        if exc.code == 429:
            raise HTTPException(
                status_code=429,
                detail=(
                    "Gemini API quota or rate limit reached. Please retry later or "
                    "review the project's free-tier limits."
                ),
            ) from exc
        if exc.code in {401, 403}:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Gemini API authentication failed. Check the backend "
                    "GEMINI_API_KEY configuration."
                ),
            ) from exc
        if exc.code in {408, 504}:
            raise HTTPException(
                status_code=504,
                detail="Gemini API request timed out. Please try again.",
            ) from exc
        raise HTTPException(
            status_code=502,
            detail="Gemini API returned an error. Please try again later.",
        ) from exc
    finally:
        await client.aio.aclose()

    content = response.text
    if not isinstance(content, str) or not content.strip():
        logger.warning("Gemini API response did not contain assistant content")
        raise HTTPException(
            status_code=502,
            detail="Gemini API returned an empty or invalid assistant response.",
        )
    return content.strip()


@app.on_event("startup")
def initialize_database():
    # Idempotent: inserts missing demo records but preserves existing conversations.
    from app.database import SessionLocal

    with SessionLocal() as session:
        seed_demo_data(session)


@app.get("/health")
def health():
    return {"status": "ok", "service": "memory-web-api"}


@app.get("/api/conversations/{conversation_id}/graph")
def get_graph(
    conversation_id: str,
    _: str = Depends(require_basic_auth),
    db: Session = Depends(get_db),
):
    nodes = db.scalars(
        select(Node)
        .where(Node.conversation_id == conversation_id)
        .order_by(Node.created_at, Node.id)
    ).all()

    node_ids = {node.id for node in nodes}
    graph_edges = db.scalars(
        select(GraphEdge)
        .where(GraphEdge.conversation_id == conversation_id)
        .order_by(GraphEdge.created_at, GraphEdge.id)
    ).all()

    # Keep the graph internally consistent even if malformed legacy data exists.
    graph_edges = [
        edge for edge in graph_edges
        if edge.source in node_ids and edge.target in node_ids
    ]

    return {
        "nodes": [node_to_dict(node) for node in nodes],
        "edges": [edge_to_dict(edge) for edge in graph_edges],
    }


def build_search_excerpt(content: str, query: str, max_chars: int = 180) -> str:
    match_index = content.lower().find(query.lower())
    excerpt_length = max(max_chars, len(query))
    if match_index < 0 or len(content) <= excerpt_length:
        return content

    context_before_match = min(60, (excerpt_length - len(query)) // 2)
    start = max(0, match_index - context_before_match)
    end = min(len(content), start + excerpt_length)
    if end == len(content):
        start = max(0, end - excerpt_length)
    excerpt = content[start:end]
    if start > 0:
        excerpt = f"…{excerpt}"
    if end < len(content):
        excerpt = f"{excerpt}…"
    return excerpt


@app.get("/api/conversations/{conversation_id}/search")
def search_conversation(
    conversation_id: str,
    q: str = Query(default=""),
    _: str = Depends(require_basic_auth),
    db: Session = Depends(get_db),
):
    query = q.strip()
    if not query:
        return {"results": []}

    terms = list(dict.fromkeys(term.lower() for term in query.split()))

    def escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    term_matches = [
        Node.content.ilike(f"%{escape_like(term)}%", escape="\\")
        for term in terms
    ]
    exact_phrase_match = Node.content.ilike(
        f"%{escape_like(query)}%",
        escape="\\",
    )
    term_match_count = sum(
        case((term_match, 1), else_=0)
        for term_match in term_matches
    )
    nodes = db.scalars(
        select(Node)
        .where(
            Node.conversation_id == conversation_id,
            Node.kind.in_(["message", "topic"]),
            or_(*term_matches),
        )
        .order_by(
            case((exact_phrase_match, 0), else_=1),
            term_match_count.desc(),
            Node.created_at,
            Node.id,
        )
    ).all()

    return {
        "results": [
            {
                "id": node.id,
                "conversation_id": node.conversation_id,
                "parent_id": node.parent_id,
                "branch_id": node.branch_id,
                "role": node.role,
                "kind": node.kind,
                "excerpt": build_search_excerpt(node.content, query),
            }
            for node in nodes
        ]
    }


@app.post("/api/follow-ups")
async def create_follow_up(
    body: FollowUpRequest,
    _: str = Depends(require_basic_auth),
    db: Session = Depends(get_db),
):
    parent = db.get(Node, body.parent_node_id)

    if parent is None:
        raise HTTPException(status_code=404, detail="Parent node not found")

    if parent.conversation_id != body.conversation_id:
        raise HTTPException(
            status_code=400,
            detail="Parent belongs to another conversation",
        )

    stripped_prompt = body.prompt.strip()
    if not stripped_prompt:
        raise HTTPException(
            status_code=422,
            detail="Prompt cannot be empty or whitespace only",
        )

    conversation = db.get(Conversation, body.conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    parent_branch = parent.branch_id or "main"
    branch_id = str(uuid4()) if body.mode == "branch" else parent_branch
    context_messages = build_follow_up_messages(db, parent, stripped_prompt)
    answer_content = await generate_assistant_response(context_messages)

    user_node = Message(
        id=str(uuid4()),
        conversation_id=body.conversation_id,
        parent_id=parent.id,
        branch_id=branch_id,
        role="user",
        content=stripped_prompt,
        kind="message",
    )
    db.add(user_node)
    db.flush()

    follow_up_edge = GraphEdge(
        id=str(uuid4()),
        conversation_id=body.conversation_id,
        source=parent.id,
        target=user_node.id,
        type="branch" if body.mode == "branch" else "continue",
    )
    db.add(follow_up_edge)

    answer_node = Message(
        id=str(uuid4()),
        conversation_id=body.conversation_id,
        parent_id=user_node.id,
        branch_id=branch_id,
        role="assistant",
        content=answer_content,
        kind="message",
    )
    db.add(answer_node)
    db.flush()

    reply_edge = GraphEdge(
        id=str(uuid4()),
        conversation_id=body.conversation_id,
        source=user_node.id,
        target=answer_node.id,
        type="reply",
    )
    db.add(reply_edge)

    try:
        db.commit()
        db.refresh(user_node)
        db.refresh(answer_node)
        db.refresh(follow_up_edge)
        db.refresh(reply_edge)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save follow-up")

    return {
        "created_nodes": [
            node_to_dict(user_node),
            node_to_dict(answer_node),
        ],
        "created_edges": [
            edge_to_dict(follow_up_edge),
            edge_to_dict(reply_edge),
        ],
    }