import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import MAX_CONTEXT_CHARS, MAX_CONTEXT_NODES, app, build_ollama_messages
from app.models import Message, Node
from app.seed import reset_demo_store


@pytest.fixture(autouse=True)
def mock_ollama(monkeypatch):
    calls = []

    def post(url, *, json, timeout):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "Mocked Ollama answer"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.main.httpx.post", post)
    return calls


@pytest.fixture()
def test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(test_engine, monkeypatch):
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_engine,
    )

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.main.seed_demo_data", lambda session: None)

    # Seed only the isolated test database.
    with TestingSessionLocal() as db:
        reset_demo_store(db)

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_demo_graph_has_nodes(client):
    response = client.get("/api/conversations/demo/graph")
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) == 3
    node_ids = {node["id"] for node in data["nodes"]}
    assert {"welcome", "answer", "docker"}.issubset(node_ids)


def test_follow_up_continue_relationship(client):
    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "answer",
        "mode": "continue",
        "prompt": "Tell me more about Docker containers.",
    })
    assert response.status_code == 200
    user_node, answer_node = response.json()["created_nodes"]
    assert user_node["parent_id"] == "answer"
    assert user_node["role"] == "user"
    assert user_node["branch_id"] == "main"
    assert user_node["content"] == "Tell me more about Docker containers."
    assert answer_node["parent_id"] == user_node["id"]
    assert answer_node["role"] == "assistant"
    assert answer_node["branch_id"] == "main"

    graph = client.get("/api/conversations/demo/graph").json()
    edges = graph["edges"]
    assert any(
        e["source"] == "answer" and e["target"] == user_node["id"]
        and e["type"] == "continue" for e in edges
    )
    assert any(
        e["source"] == user_node["id"] and e["target"] == answer_node["id"]
        and e["type"] == "reply" for e in edges
    )
    assert any(e["source"] == "welcome" and e["target"] == "answer" for e in edges)
    assert any(e["source"] == "answer" and e["target"] == "docker" for e in edges)


def test_ollama_generation_uses_ordered_context_and_persists_response(client, mock_ollama):
    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "docker",
        "mode": "continue",
        "prompt": "How does this relate to the discussion?",
    })
    assert response.status_code == 200
    user_node, answer_node = response.json()["created_nodes"]
    assert answer_node["content"] == "Mocked Ollama answer"

    request = mock_ollama[0]
    assert request["url"] == "http://127.0.0.1:11434/api/chat"
    assert request["timeout"] == 120
    assert request["json"]["model"] == "qwen2.5:7b"
    assert request["json"]["stream"] is False
    messages = request["json"]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1] == {
        "role": "user",
        "content": "Explain microservices architecture.",
    }
    assert messages[2]["role"] == "assistant"
    assert messages[3] == {
        "role": "system",
        "content": (
            "Supporting topic context: "
            "Docker: package an application and its dependencies into containers."
        ),
    }
    assert messages[-1] == {
        "role": "user",
        "content": "How does this relate to the discussion?",
    }
    graph = client.get("/api/conversations/demo/graph").json()
    saved_answer = next(node for node in graph["nodes"] if node["id"] == answer_node["id"])
    assert saved_answer["content"] == "Mocked Ollama answer"
    assert any(edge["target"] == user_node["id"] for edge in graph["edges"])


def test_ollama_settings_are_configurable(client, monkeypatch, mock_ollama):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b-custom")
    monkeypatch.setenv("OLLAMA_TIMEOUT", "45.5")

    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "answer",
        "mode": "continue",
        "prompt": "Check configured Ollama settings",
    })
    assert response.status_code == 200
    assert mock_ollama[0]["url"] == "http://localhost:11434/api/chat"
    assert mock_ollama[0]["json"]["model"] == "qwen2.5:7b-custom"
    assert mock_ollama[0]["timeout"] == 45.5


def test_context_follows_only_selected_branch_ancestry(client, mock_ollama):
    first_branch = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "answer",
        "mode": "branch",
        "prompt": "First branch only",
    })
    second_branch = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "answer",
        "mode": "branch",
        "prompt": "Sibling branch secret",
    })
    first_branch_answer = first_branch.json()["created_nodes"][1]
    continuation = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": first_branch_answer["id"],
        "mode": "continue",
        "prompt": "Continue this branch",
    })
    assert first_branch.status_code == second_branch.status_code == continuation.status_code == 200

    messages = mock_ollama[2]["json"]["messages"]
    contents = [message["content"] for message in messages]
    assert "First branch only" in contents
    assert "Sibling branch secret" not in contents
    assert contents.index("Explain microservices architecture.") < contents.index(
        "Microservices split an application into independently deployable services. "
        "Common related topics include APIs, databases, and Docker."
    ) < contents.index("First branch only") < contents.index("Continue this branch")


def test_continue_and_branch_from_same_parent_use_same_context(client, mock_ollama):
    prompt = "Compare two deployment options."
    branch = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "answer",
        "mode": "branch",
        "prompt": prompt,
    })
    continuation = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "answer",
        "mode": "continue",
        "prompt": prompt,
    })

    assert branch.status_code == continuation.status_code == 200
    assert mock_ollama[0]["json"]["messages"] == mock_ollama[1]["json"]["messages"]

    branch_user, branch_answer = branch.json()["created_nodes"]
    continue_user, continue_answer = continuation.json()["created_nodes"]
    assert branch_user["branch_id"] != "main"
    assert branch_answer["branch_id"] == branch_user["branch_id"]
    assert continue_user["branch_id"] == "main"
    assert continue_answer["branch_id"] == "main"

    branch_edge = branch.json()["created_edges"][0]
    continue_edge = continuation.json()["created_edges"][0]
    assert branch_edge["type"] == "branch"
    assert continue_edge["type"] == "continue"


def test_context_stops_at_cyclic_ancestry(client, test_engine):
    with Session(test_engine) as db:
        welcome = db.get(Node, "welcome")
        assert welcome is not None
        welcome.parent_id = "answer"
        db.commit()

        parent = db.get(Node, "docker")
        assert parent is not None
        messages = build_ollama_messages(db, parent, "Cycle check")

    assert messages[0]["role"] == "system"
    assert [message["content"] for message in messages[1:]] == [
        "Explain microservices architecture.",
        (
            "Microservices split an application into independently deployable services. "
            "Common related topics include APIs, databases, and Docker."
        ),
        (
            "Supporting topic context: "
            "Docker: package an application and its dependencies into containers."
        ),
        "Cycle check",
    ]
    assert len(messages) == 5


def test_context_stops_when_ancestor_is_missing(client, test_engine):
    class MissingAncestorSession:
        def __init__(self, session, missing_id):
            self.session = session
            self.missing_id = missing_id

        def get(self, model, identity):
            if identity == self.missing_id:
                return None
            return self.session.get(model, identity)

    with Session(test_engine) as db:
        parent = db.get(Node, "answer")
        assert parent is not None
        messages = build_ollama_messages(
            MissingAncestorSession(db, "welcome"),
            parent,
            "Missing ancestor check",
        )

    assert [message["content"] for message in messages[1:]] == [
        parent.content,
        "Missing ancestor check",
    ]


def test_context_is_chronological_and_limited_to_32_ancestry_nodes(
    client, test_engine
):
    assert MAX_CONTEXT_NODES == 32
    with Session(test_engine) as db:
        parent_id = None
        for index in range(MAX_CONTEXT_NODES + 2):
            node = Message(
                id=f"history-{index:02d}",
                conversation_id="demo",
                parent_id=parent_id,
                branch_id="main",
                role="user" if index % 2 == 0 else "assistant",
                content=f"history-{index:02d}",
                kind="message",
            )
            db.add(node)
            parent_id = node.id
        db.commit()

        parent = db.get(Node, parent_id)
        assert parent is not None
        messages = build_ollama_messages(db, parent, "Node limit check")

    context = messages[1:-1]
    assert len(context) == MAX_CONTEXT_NODES
    assert [message["content"] for message in context] == [
        f"history-{index:02d}" for index in range(2, MAX_CONTEXT_NODES + 2)
    ]
    assert messages[-1]["content"] == "Node limit check"


def test_context_character_limit_truncates_oldest_content_first(
    client, test_engine
):
    assert MAX_CONTEXT_CHARS == 12000
    oldest_content = "oldest:" + "o" * 4993
    middle_content = "middle:" + "m" * 4993
    newest_content = "newest:" + "n" * 4993
    with Session(test_engine) as db:
        oldest = Message(
            id="char-oldest",
            conversation_id="demo",
            parent_id=None,
            branch_id="main",
            role="user",
            content=oldest_content,
            kind="message",
        )
        middle = Message(
            id="char-middle",
            conversation_id="demo",
            parent_id=oldest.id,
            branch_id="main",
            role="assistant",
            content=middle_content,
            kind="message",
        )
        newest = Message(
            id="char-newest",
            conversation_id="demo",
            parent_id=middle.id,
            branch_id="main",
            role="user",
            content=newest_content,
            kind="message",
        )
        db.add_all([oldest, middle, newest])
        db.commit()

        parent = db.get(Node, newest.id)
        assert parent is not None
        messages = build_ollama_messages(db, parent, "Character limit check")

    context = messages[1:-1]
    assert len(context) == 3
    assert [message["content"] for message in context] == [
        oldest_content[-(MAX_CONTEXT_CHARS - 2 * 5000):],
        middle_content,
        newest_content,
    ]
    assert sum(len(message["content"]) for message in context) == MAX_CONTEXT_CHARS
    assert messages[-1]["content"] == "Character limit check"


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
def test_ollama_connection_failure_or_timeout_does_not_persist(
    client, monkeypatch, error_type
):
    def fail_request(*args, **kwargs):
        raise error_type("Ollama unavailable")

    monkeypatch.setattr("app.main.httpx.post", fail_request)
    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "answer",
        "mode": "continue",
        "prompt": "This should not be saved",
    })
    assert response.status_code == 503
    assert "Ollama" in response.json()["detail"]
    graph = client.get("/api/conversations/demo/graph").json()
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 2


@pytest.mark.parametrize(
    "ollama_response",
    [
        httpx.Response(
            200, content=b"not JSON", request=httpx.Request("POST", "http://localhost")
        ),
        httpx.Response(
            200,
            json={"message": {"content": "  "}},
            request=httpx.Request("POST", "http://localhost"),
        ),
        httpx.Response(
            200,
            json={"message": {"content": 42}},
            request=httpx.Request("POST", "http://localhost"),
        ),
    ],
)
def test_invalid_or_empty_ollama_response_does_not_persist(
    client, monkeypatch, ollama_response
):
    monkeypatch.setattr("app.main.httpx.post", lambda *args, **kwargs: ollama_response)
    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "answer",
        "mode": "continue",
        "prompt": "This should not be saved",
    })
    assert response.status_code == 502
    graph = client.get("/api/conversations/demo/graph").json()
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 2


def test_follow_up_branch_relationship(client):
    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "welcome",
        "mode": "branch",
        "prompt": "Compare microservices with monoliths.",
    })
    assert response.status_code == 200
    user_node, answer_node = response.json()["created_nodes"]
    assert user_node["parent_id"] == "welcome"
    assert user_node["role"] == "user"
    assert user_node["branch_id"] != "main"
    assert answer_node["parent_id"] == user_node["id"]
    assert answer_node["branch_id"] == user_node["branch_id"]

    graph = client.get("/api/conversations/demo/graph").json()
    edges = graph["edges"]
    assert any(
        e["source"] == "welcome" and e["target"] == user_node["id"]
        and e["type"] == "branch" for e in edges
    )
    assert any(
        e["source"] == user_node["id"] and e["target"] == answer_node["id"]
        and e["type"] == "reply" for e in edges
    )


def test_continue_from_branch_preserves_branch_ancestry(client):
    branch = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "answer",
        "mode": "branch",
        "prompt": "What about serverless?",
    })
    assert branch.status_code == 200
    branch_answer = branch.json()["created_nodes"][1]
    branch_id = branch_answer["branch_id"]

    continuation = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": branch_answer["id"],
        "mode": "continue",
        "prompt": "Give examples of serverless providers.",
    })
    assert continuation.status_code == 200
    user_node, answer_node = continuation.json()["created_nodes"]
    assert user_node["parent_id"] == branch_answer["id"]
    assert user_node["branch_id"] == branch_id
    assert answer_node["parent_id"] == user_node["id"]
    assert answer_node["branch_id"] == branch_id


def test_follow_up_requires_valid_parent(client):
    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "missing-id-123",
        "mode": "branch",
        "prompt": "What is a container?",
    })
    assert response.status_code == 404
    assert response.json()["detail"] == "Parent node not found"


def test_parent_in_different_conversation_rejected(client):
    response = client.post("/api/follow-ups", json={
        "conversation_id": "another_conv",
        "parent_node_id": "welcome",
        "mode": "continue",
        "prompt": "Hello?",
    })
    assert response.status_code == 400
    assert response.json()["detail"] == "Parent belongs to another conversation"


def test_invalid_mode_rejected(client):
    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "welcome",
        "mode": "invalid_mode",
        "prompt": "Test",
    })
    assert response.status_code == 422


def test_blank_prompt_rejected(client):
    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "welcome",
        "mode": "branch",
        "prompt": "   ",
    })
    assert response.status_code == 422


def test_branch_from_internal_node_with_children_preserves_existing(client):
    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "welcome",
        "mode": "branch",
        "prompt": "Branch off the welcome message.",
    })
    assert response.status_code == 200
    user_node, answer_node = response.json()["created_nodes"]
    assert user_node["parent_id"] == "welcome"
    assert user_node["branch_id"] != "main"
    assert answer_node["parent_id"] == user_node["id"]
    assert answer_node["branch_id"] == user_node["branch_id"]

    graph = client.get("/api/conversations/demo/graph").json()
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["welcome"]["content"] == "Explain microservices architecture."
    assert nodes["answer"]["content"].startswith("Microservices split")
    assert nodes["docker"]["content"].startswith("Docker:")
    assert nodes["welcome"]["branch_id"] == "main"
    assert nodes["answer"]["branch_id"] == "main"
    assert nodes["docker"]["branch_id"] == "main"


def test_multiple_branches_from_same_node_have_distinct_identities(client):
    first = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "answer",
        "mode": "branch",
        "prompt": "Tell me about Kubernetes.",
    })
    second = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "answer",
        "mode": "branch",
        "prompt": "Tell me about Nomad.",
    })
    assert first.status_code == second.status_code == 200
    first_user, first_answer = first.json()["created_nodes"]
    second_user, second_answer = second.json()["created_nodes"]
    assert first_user["parent_id"] == second_user["parent_id"] == "answer"
    assert first_user["branch_id"] != "main"
    assert second_user["branch_id"] != "main"
    assert first_user["branch_id"] != second_user["branch_id"]
    assert first_answer["branch_id"] == first_user["branch_id"]
    assert second_answer["branch_id"] == second_user["branch_id"]


def test_multi_step_continuation_ancestry(client):
    parent_id = "answer"
    for prompt in [
        "Tell me about API Gateways.",
        "Compare Kong and Envoy.",
        "Which is best for Kubernetes?",
    ]:
        response = client.post("/api/follow-ups", json={
            "conversation_id": "demo",
            "parent_node_id": parent_id,
            "mode": "continue",
            "prompt": prompt,
        })
        assert response.status_code == 200
        user_node, answer_node = response.json()["created_nodes"]
        assert user_node["parent_id"] == parent_id
        assert user_node["branch_id"] == "main"
        assert answer_node["parent_id"] == user_node["id"]
        assert answer_node["branch_id"] == "main"
        parent_id = answer_node["id"]


def test_empty_string_parent_node_rejected(client):
    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "",
        "mode": "branch",
        "prompt": "Test empty parent",
    })
    assert response.status_code == 404


def test_missing_parent_node_field_rejected(client):
    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "mode": "branch",
        "prompt": "Test missing parent field",
    })
    assert response.status_code == 422


def test_state_isolation_starts_with_clean_seed_data(client):
    graph = client.get("/api/conversations/demo/graph").json()
    assert len(graph["nodes"]) == 3
    assert {node["id"] for node in graph["nodes"]} == {"welcome", "answer", "docker"}


def test_complex_graph_topology_and_edge_referential_integrity(client):
    for parent_id, mode, prompt in [
        ("welcome", "branch", "Branch 1 prompt"),
        ("answer", "branch", "Branch 2 prompt"),
        ("docker", "continue", "Continue from docker"),
    ]:
        response = client.post("/api/follow-ups", json={
            "conversation_id": "demo",
            "parent_node_id": parent_id,
            "mode": mode,
            "prompt": prompt,
        })
        assert response.status_code == 200

    graph = client.get("/api/conversations/demo/graph").json()
    nodes = graph["nodes"]
    edges = graph["edges"]
    node_ids = {node["id"] for node in nodes}
    assert len(nodes) == 9
    assert len(edges) == 8
    for edge in edges:
        assert edge["source"] in node_ids
        assert edge["target"] in node_ids
        assert edge["type"] in {"reply", "mentions", "branch", "continue"}


def test_database_persists_follow_up_across_sessions(client, test_engine):
    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "answer",
        "mode": "continue",
        "prompt": "Check persistence.",
    })
    assert response.status_code == 200
    created_id = response.json()["created_nodes"][0]["id"]

    graph = client.get("/api/conversations/demo/graph").json()
    assert any(node["id"] == created_id for node in graph["nodes"])