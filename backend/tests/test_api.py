import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.seed import reset_demo_store


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
def client(test_engine):
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