from sqlalchemy import select, delete
from sqlalchemy.orm import Session
from app.models import Conversation, Node, Message, TopicNode, GraphEdge


def seed_demo_data(session: Session) -> None:
    # 1. Ensure demo conversation exists
    demo_conv = session.get(Conversation, "demo")
    if not demo_conv:
        demo_conv = Conversation(id="demo", title="Microservices & Containers Demo")
        session.add(demo_conv)
        session.flush()

    # 2. Seed initial nodes idempotently
    welcome = session.get(Node, "welcome")
    if not welcome:
        welcome = Message(
            id="welcome",
            conversation_id="demo",
            parent_id=None,
            branch_id="main",
            role="user",
            content="Explain microservices architecture.",
            kind="message",
        )
        session.add(welcome)

    answer = session.get(Node, "answer")
    if not answer:
        answer = Message(
            id="answer",
            conversation_id="demo",
            parent_id="welcome",
            branch_id="main",
            role="assistant",
            content=(
                "Microservices split an application into independently deployable services. "
                "Common related topics include APIs, databases, and Docker."
            ),
            kind="message",
        )
        session.add(answer)

    docker = session.get(Node, "docker")
    if not docker:
        docker = TopicNode(
            id="docker",
            conversation_id="demo",
            parent_id="answer",
            branch_id="main",
            role="topic",
            content="Docker: package an application and its dependencies into containers.",
            kind="topic",
        )
        session.add(docker)

    session.flush()

    # 3. Seed initial edges idempotently
    e1 = session.get(GraphEdge, "e1")
    if not e1:
        e1 = GraphEdge(
            id="e1",
            conversation_id="demo",
            source="welcome",
            target="answer",
            type="reply",
        )
        session.add(e1)

    e2 = session.get(GraphEdge, "e2")
    if not e2:
        e2 = GraphEdge(
            id="e2",
            conversation_id="demo",
            source="answer",
            target="docker",
            type="mentions",
        )
        session.add(e2)

    session.commit()


def reset_demo_store(session: Session) -> None:
    """Helper used during tests to reset the demo conversation to exact seed state."""
    # Delete all edges and nodes belonging to demo conversation
    session.execute(delete(GraphEdge).where(GraphEdge.conversation_id == "demo"))
    session.execute(delete(Node).where(Node.conversation_id == "demo"))
    session.execute(delete(Conversation).where(Conversation.id == "demo"))
    session.commit()

    # Re-seed
    seed_demo_data(session)
