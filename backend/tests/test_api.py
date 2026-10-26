import base64
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import MAX_CONTEXT_CHARS, MAX_CONTEXT_NODES, app, build_follow_up_messages
from app.models import GraphEdge, Message, Node
from app.seed import reset_demo_store

TEST_USERNAME = "memory-web-test-user"
TEST_PASSWORD = "memory-web-test-password"


def basic_auth_header(username=TEST_USERNAME, password=TEST_PASSWORD):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


PROTECTED_ENDPOINTS = [
    ("GET", "/api/conversations/demo/graph"),
    ("GET", "/api/conversations/demo/search"),
    (
        "POST",
        "/api/follow-ups",
    ),
]


def request_protected_endpoint(client, method, path, headers=None):
    if path.endswith("/search"):
        return client.request(method, path, params={"q": "docker"}, headers=headers)
    if method == "POST":
        return client.request(
            method,
            path,
            json={
                "conversation_id": "demo",
                "parent_node_id": "answer",
                "mode": "continue",
                "prompt": "Authenticated request test",
            },
            headers=headers,
        )
    return client.request(method, path, headers=headers)


@pytest.fixture(autouse=True)
def mock_gemini(monkeypatch):
    calls = []
    client_options = []
    closed_clients = []
    state = {"failure": None, "response_text": "Mocked Gemini answer"}

    class FakeAsyncModels:
        async def generate_content(self, **request):
            calls.append(request)
            if state["failure"] is not None:
                raise state["failure"]
            return SimpleNamespace(text=state["response_text"])

    class FakeAsyncClient:
        def __init__(self):
            self.models = FakeAsyncModels()

        async def aclose(self):
            closed_clients.append(True)

    class FakeClient:
        def __init__(self, *, api_key, http_options):
            client_options.append({"key_configured": bool(api_key), "http_options": http_options})
            self.aio = FakeAsyncClient()

    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-api-key")
    monkeypatch.setattr("app.main.genai.Client", FakeClient)
    return {
        "calls": calls,
        "client_options": client_options,
        "closed_clients": closed_clients,
        "state": state,
    }


def unpack_gemini_contents(request):
    return [
        {
            "role": content.role,
            "content": content.parts[0].text,
        }
        for content in request["contents"]
    ]


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
    monkeypatch.setenv("MEMORY_WEB_USERNAME", TEST_USERNAME)
    monkeypatch.setenv("MEMORY_WEB_PASSWORD", TEST_PASSWORD)

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

    with TestClient(app, headers={"Authorization": basic_auth_header()}) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize(("method", "path"), PROTECTED_ENDPOINTS)
def test_protected_endpoints_reject_missing_credentials(client, method, path):
    client.headers.pop("Authorization")
    response = request_protected_endpoint(client, method, path)

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")


@pytest.mark.parametrize(("method", "path"), PROTECTED_ENDPOINTS)
def test_protected_endpoints_reject_invalid_credentials(client, method, path):
    response = request_protected_endpoint(
        client,
        method,
        path,
        headers={"Authorization": basic_auth_header(TEST_USERNAME, "wrong-password")},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")


@pytest.mark.parametrize(("method", "path"), PROTECTED_ENDPOINTS)
def test_protected_endpoints_accept_valid_credentials(client, method, path):
    response = request_protected_endpoint(client, method, path)

    assert response.status_code == 200


@pytest.mark.parametrize(("method", "path"), PROTECTED_ENDPOINTS)
def test_protected_endpoints_fail_closed_without_server_credentials(
    client,
    monkeypatch,
    method,
    path,
):
    monkeypatch.delenv("MEMORY_WEB_USERNAME")
    monkeypatch.delenv("MEMORY_WEB_PASSWORD")

    response = request_protected_endpoint(client, method, path)

    assert response.status_code == 503
    assert response.json()["detail"] == "API authentication is not configured"


@pytest.mark.parametrize("invalid_credentials", [False, True])
def test_rejected_follow_up_does_not_call_gemini_or_write_records(
    client,
    test_engine,
    mock_gemini,
    invalid_credentials,
):
    headers = None
    if invalid_credentials:
        headers = {
            "Authorization": basic_auth_header(TEST_USERNAME, "wrong-password"),
        }
    else:
        client.headers.pop("Authorization")
    with Session(test_engine) as db:
        before_nodes = db.scalar(select(func.count()).select_from(Node))
        before_edges = db.scalar(select(func.count()).select_from(GraphEdge))

    response = request_protected_endpoint(
        client,
        "POST",
        "/api/follow-ups",
        headers=headers,
    )

    with Session(test_engine) as db:
        after_nodes = db.scalar(select(func.count()).select_from(Node))
        after_edges = db.scalar(select(func.count()).select_from(GraphEdge))

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")
    assert mock_gemini["calls"] == []
    assert after_nodes == before_nodes
    assert after_edges == before_edges


def test_cors_allows_pages_origin_and_authorization_header(client):
    response = client.options(
        "/api/conversations/demo/graph",
        headers={
            "Origin": "https://memory-web-cvn.pages.dev",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "https://memory-web-cvn.pages.dev"
    )
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_demo_graph_has_nodes(client):
    response = client.get("/api/conversations/demo/graph")
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) == 3
    node_ids = {node["id"] for node in data["nodes"]}
    assert {"welcome", "answer", "docker"}.issubset(node_ids)


def test_search_matches_message_and_topic_content_case_insensitively(client):
    response = client.get(
        "/api/conversations/demo/search",
        params={"q": "dOcKeR"},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert {result["id"] for result in results} == {"answer", "docker"}
    answer = next(result for result in results if result["id"] == "answer")
    topic = next(result for result in results if result["id"] == "docker")
    assert answer["role"] == "assistant"
    assert answer["kind"] == "message"
    assert answer["conversation_id"] == "demo"
    assert answer["parent_id"] == "welcome"
    assert answer["branch_id"] == "main"
    assert "Docker" in answer["excerpt"]
    assert topic["role"] == "topic"
    assert topic["kind"] == "topic"
    assert "Docker:" in topic["excerpt"]


def test_search_prioritizes_exact_phrase_over_more_relevant_partial_match(client, test_engine):
    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with Session(test_engine) as db:
        db.add_all([
            Message(
                id="partial-exact-rank",
                conversation_id="demo",
                parent_id="answer",
                branch_id="main",
                role="user",
                content="rankneedle something phraseanchor",
                kind="message",
                created_at=created_at,
            ),
            Message(
                id="exact-phrase-rank",
                conversation_id="demo",
                parent_id="answer",
                branch_id="main",
                role="user",
                content="rankneedle phraseanchor",
                kind="message",
                created_at=created_at,
            ),
        ])
        db.commit()

    response = client.get(
        "/api/conversations/demo/search",
        params={"q": "rankneedle phraseanchor"},
    )

    assert response.status_code == 200
    ids = [result["id"] for result in response.json()["results"]]
    assert ids[:2] == ["exact-phrase-rank", "partial-exact-rank"]


def test_search_ranks_partial_matches_by_distinct_query_terms(client, test_engine):
    with Session(test_engine) as db:
        db.add_all([
            Message(
                id="partial-one-term",
                conversation_id="demo",
                parent_id="answer",
                branch_id="main",
                role="user",
                content="rankquartz appears",
                kind="message",
            ),
            Message(
                id="partial-three-terms",
                conversation_id="demo",
                parent_id="answer",
                branch_id="main",
                role="user",
                content="rankmaple then rankquartz and rankcedar",
                kind="message",
            ),
            Message(
                id="partial-two-terms",
                conversation_id="demo",
                parent_id="answer",
                branch_id="main",
                role="user",
                content="rankcedar with rankmaple",
                kind="message",
            ),
        ])
        db.commit()

    response = client.get(
        "/api/conversations/demo/search",
        params={"q": "rankquartz rankcedar rankmaple"},
    )

    assert response.status_code == 200
    ids = [result["id"] for result in response.json()["results"]]
    assert ids[:3] == [
        "partial-three-terms",
        "partial-two-terms",
        "partial-one-term",
    ]


def test_search_orders_equal_rank_matches_by_created_at_then_id(client, test_engine):
    earlier = datetime(2025, 1, 1, tzinfo=timezone.utc)
    later = datetime(2025, 1, 2, tzinfo=timezone.utc)
    with Session(test_engine) as db:
        db.add_all([
            Message(
                id="tie-z",
                conversation_id="demo",
                parent_id="answer",
                branch_id="main",
                role="user",
                content="rankcomet found",
                kind="message",
                created_at=earlier,
            ),
            Message(
                id="tie-b",
                conversation_id="demo",
                parent_id="answer",
                branch_id="main",
                role="user",
                content="rankcomet found",
                kind="message",
                created_at=earlier,
            ),
            Message(
                id="tie-a",
                conversation_id="demo",
                parent_id="answer",
                branch_id="main",
                role="user",
                content="rankcomet found",
                kind="message",
                created_at=earlier,
            ),
            Message(
                id="tie-older",
                conversation_id="demo",
                parent_id="answer",
                branch_id="main",
                role="user",
                content="rankcomet found",
                kind="message",
                created_at=later,
            ),
        ])
        db.commit()

    response = client.get(
        "/api/conversations/demo/search",
        params={"q": "rankcomet absent"},
    )

    assert response.status_code == 200
    ids = [result["id"] for result in response.json()["results"]]
    assert ids[:4] == ["tie-a", "tie-b", "tie-z", "tie-older"]


def test_search_counts_repeated_query_terms_once(client, test_engine):
    with Session(test_engine) as db:
        db.add_all([
            Message(
                id="repeat-one-distinct-term",
                conversation_id="demo",
                parent_id="answer",
                branch_id="main",
                role="user",
                content="rankivy",
                kind="message",
            ),
            Message(
                id="repeat-two-distinct-terms",
                conversation_id="demo",
                parent_id="answer",
                branch_id="main",
                role="user",
                content="rankivy and rankcedar",
                kind="message",
            ),
        ])
        db.commit()

    response = client.get(
        "/api/conversations/demo/search",
        params={"q": "rankivy rankivy rankcedar"},
    )

    assert response.status_code == 200
    ids = [result["id"] for result in response.json()["results"]]
    assert ids[:2] == ["repeat-two-distinct-terms", "repeat-one-distinct-term"]


def test_search_treats_like_special_characters_literally(client, test_engine):
    query = r"literal%_back\slash"
    with Session(test_engine) as db:
        db.add_all([
            Message(
                id="literal-special-match",
                conversation_id="demo",
                parent_id="answer",
                branch_id="main",
                role="user",
                content=f"Contains {query} exactly",
                kind="message",
            ),
            Message(
                id="percent-wildcard-decoy",
                conversation_id="demo",
                parent_id="answer",
                branch_id="main",
                role="user",
                content=r"literalx_back\slash",
                kind="message",
            ),
            Message(
                id="underscore-wildcard-decoy",
                conversation_id="demo",
                parent_id="answer",
                branch_id="main",
                role="user",
                content=r"literal%Xback\slash",
                kind="message",
            ),
            Message(
                id="backslash-escape-decoy",
                conversation_id="demo",
                parent_id="answer",
                branch_id="main",
                role="user",
                content="literal%_backslash",
                kind="message",
            ),
        ])
        db.commit()

    response = client.get(
        "/api/conversations/demo/search",
        params={"q": query},
    )

    assert response.status_code == 200
    assert [result["id"] for result in response.json()["results"]] == [
        "literal-special-match",
    ]


@pytest.mark.parametrize("query", ["", "   "])
def test_search_empty_query_returns_no_results(client, query):
    response = client.get(
        "/api/conversations/demo/search",
        params={"q": query},
    )

    assert response.status_code == 200
    assert response.json() == {"results": []}


def test_search_with_no_matches_returns_empty_results(client):
    response = client.get(
        "/api/conversations/demo/search",
        params={"q": "no matching node content"},
    )

    assert response.status_code == 200
    assert response.json() == {"results": []}


def test_search_returns_match_centered_excerpt_for_long_content(client, test_engine):
    content = f"{'x' * 240}distinctive search phrase{'y' * 240}"
    with Session(test_engine) as db:
        db.add(Message(
            id="long-search-result",
            conversation_id="demo",
            parent_id="answer",
            branch_id="main",
            role="user",
            content=content,
            kind="message",
        ))
        db.commit()

    response = client.get(
        "/api/conversations/demo/search",
        params={"q": "DISTINCTIVE SEARCH PHRASE"},
    )

    assert response.status_code == 200
    result = next(
        item for item in response.json()["results"]
        if item["id"] == "long-search-result"
    )
    assert len(result["excerpt"]) <= 182
    assert result["excerpt"].startswith("…")
    assert result["excerpt"].endswith("…")
    assert "distinctive search phrase" in result["excerpt"]


def test_search_excerpt_keeps_a_long_query_intact(client, test_engine):
    query = f"distinctive {'search ' * 30}phrase"
    content = f"{'x' * 240}{query}{'y' * 240}"
    with Session(test_engine) as db:
        db.add(Message(
            id="long-query-result",
            conversation_id="demo",
            parent_id="answer",
            branch_id="main",
            role="user",
            content=content,
            kind="message",
        ))
        db.commit()

    response = client.get(
        "/api/conversations/demo/search",
        params={"q": query},
    )

    assert response.status_code == 200
    result = next(
        item for item in response.json()["results"]
        if item["id"] == "long-query-result"
    )
    assert query in result["excerpt"]
    assert len(result["excerpt"]) <= len(query) + 2


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


def test_gemini_generation_uses_ordered_context_and_persists_response(
    client,
    mock_gemini,
):
    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "docker",
        "mode": "continue",
        "prompt": "How does this relate to the discussion?",
    })
    assert response.status_code == 200
    user_node, answer_node = response.json()["created_nodes"]
    assert answer_node["content"] == "Mocked Gemini answer"

    request = mock_gemini["calls"][0]
    assert request["model"] == "gemini-3.5-flash-lite"
    assert request["config"].system_instruction == (
        "Continue the conversation helpfully. Topic entries are supporting context, "
        "not prior user or assistant turns."
    )
    assert mock_gemini["client_options"][0]["key_configured"] is True
    assert mock_gemini["client_options"][0]["http_options"].timeout == 60000
    messages = unpack_gemini_contents(request)
    assert messages[1] == {
        "role": "model",
        "content": "Microservices split an application into independently deployable services. "
        "Common related topics include APIs, databases, and Docker.",
    }
    assert messages[0] == {
        "role": "user",
        "content": "Explain microservices architecture.",
    }
    assert messages[2] == {
        "role": "user",
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
    assert saved_answer["content"] == "Mocked Gemini answer"
    assert any(edge["target"] == user_node["id"] for edge in graph["edges"])
    assert mock_gemini["closed_clients"] == [True]


def test_gemini_settings_are_configurable(client, monkeypatch, mock_gemini):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-configured-model")
    monkeypatch.setenv("GEMINI_TIMEOUT", "45.5")

    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "answer",
        "mode": "continue",
        "prompt": "Check configured Gemini settings",
    })
    assert response.status_code == 200
    assert mock_gemini["calls"][0]["model"] == "gemini-configured-model"
    assert mock_gemini["client_options"][0]["http_options"].timeout == 45500


def test_context_follows_only_selected_branch_ancestry(client, mock_gemini):
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

    messages = unpack_gemini_contents(mock_gemini["calls"][2])
    contents = [message["content"] for message in messages]
    assert "First branch only" in contents
    assert "Sibling branch secret" not in contents
    assert contents.index("Explain microservices architecture.") < contents.index(
        "Microservices split an application into independently deployable services. "
        "Common related topics include APIs, databases, and Docker."
    ) < contents.index("First branch only") < contents.index("Continue this branch")


def test_continue_and_branch_from_same_parent_use_same_context(client, mock_gemini):
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
    assert mock_gemini["calls"][0]["contents"] == mock_gemini["calls"][1]["contents"]
    assert mock_gemini["calls"][0]["config"] == mock_gemini["calls"][1]["config"]

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
        messages = build_follow_up_messages(db, parent, "Cycle check")

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
        messages = build_follow_up_messages(
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
        messages = build_follow_up_messages(db, parent, "Node limit check")

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
        messages = build_follow_up_messages(db, parent, "Character limit check")

    context = messages[1:-1]
    assert len(context) == 3
    assert [message["content"] for message in context] == [
        oldest_content[-(MAX_CONTEXT_CHARS - 2 * 5000):],
        middle_content,
        newest_content,
    ]
    assert sum(len(message["content"]) for message in context) == MAX_CONTEXT_CHARS
    assert messages[-1]["content"] == "Character limit check"


@pytest.mark.parametrize(
    ("error_type", "status_code"),
    [(httpx.ConnectError, 502), (httpx.ReadTimeout, 504)],
)
def test_gemini_connection_failure_or_timeout_does_not_persist(
    client,
    mock_gemini,
    error_type,
    status_code,
):
    mock_gemini["state"]["failure"] = error_type("Upstream failure")
    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "answer",
        "mode": "continue",
        "prompt": "This should not be saved",
    })
    assert response.status_code == status_code
    assert "Gemini" in response.json()["detail"]
    graph = client.get("/api/conversations/demo/graph").json()
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 2


@pytest.mark.parametrize(
    "response_text",
    ["", "  ", None, 42],
)
def test_invalid_or_empty_gemini_response_does_not_persist(
    client,
    mock_gemini,
    response_text,
):
    mock_gemini["state"]["response_text"] = response_text
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


@pytest.mark.parametrize(
    ("status_code", "expected_status", "expected_detail"),
    [
        (429, 429, "quota or rate limit"),
        (401, 503, "authentication failed"),
        (403, 503, "authentication failed"),
        (500, 502, "Gemini API returned an error"),
    ],
)
def test_gemini_api_errors_are_safe_and_do_not_persist(
    client,
    monkeypatch,
    mock_gemini,
    status_code,
    expected_status,
    expected_detail,
):
    class FakeAPIError(Exception):
        def __init__(self, code):
            super().__init__("internal upstream error")
            self.code = code

    monkeypatch.setattr("app.main.errors.APIError", FakeAPIError)
    mock_gemini["state"]["failure"] = FakeAPIError(status_code)
    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "answer",
        "mode": "continue",
        "prompt": "This should not be saved",
    })

    assert response.status_code == expected_status
    assert expected_detail in response.json()["detail"]
    assert "test-gemini-api-key" not in response.text
    graph = client.get("/api/conversations/demo/graph").json()
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 2


def test_missing_gemini_key_returns_safe_configuration_error(
    client,
    monkeypatch,
    mock_gemini,
):
    monkeypatch.delenv("GEMINI_API_KEY")
    response = client.post("/api/follow-ups", json={
        "conversation_id": "demo",
        "parent_node_id": "answer",
        "mode": "continue",
        "prompt": "This should not be saved",
    })

    assert response.status_code == 503
    assert "GEMINI_API_KEY" in response.json()["detail"]
    assert "test-gemini-api-key" not in response.text
    assert mock_gemini["calls"] == []
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