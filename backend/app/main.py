from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Conversation, Node, Message, TopicNode, GraphEdge
from app.seed import seed_demo_data

app = FastAPI(title="Memory Web API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class FollowUpRequest(BaseModel):
    conversation_id: str = "demo"
    parent_node_id: str
    mode: str = Field(pattern="^(continue|branch)$")
    prompt: str = Field(min_length=1, max_length=8000)


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
def get_graph(conversation_id: str, db: Session = Depends(get_db)):
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


@app.post("/api/follow-ups")
def create_follow_up(body: FollowUpRequest, db: Session = Depends(get_db)):
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

    # Placeholder only; real LLM context assembly comes in a later milestone.
    answer_node = Message(
        id=str(uuid4()),
        conversation_id=body.conversation_id,
        parent_id=user_node.id,
        branch_id=branch_id,
        role="assistant",
        content=(
            f"Demo response to: {stripped_prompt}\n\n"
            f"Context anchor: {parent.content[:240]}"
        ),
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