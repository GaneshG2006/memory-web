from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        onupdate=utc_now,
        nullable=False,
    )

    nodes: Mapped[list["Node"]] = relationship(
        "Node", back_populates="conversation", cascade="all, delete-orphan"
    )
    edges: Mapped[list["GraphEdge"]] = relationship(
        "GraphEdge", back_populates="conversation", cascade="all, delete-orphan"
    )


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    branch_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="main", index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="message")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        onupdate=utc_now,
        nullable=False,
    )

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="nodes")
    parent: Mapped["Node | None"] = relationship(
        "Node", remote_side=[id], back_populates="children"
    )
    children: Mapped[list["Node"]] = relationship("Node", back_populates="parent")

    __mapper_args__ = {
        "polymorphic_on": "kind",
        "polymorphic_identity": "node",
    }


class Message(Node):
    __mapper_args__ = {
        "polymorphic_identity": "message",
    }


class TopicNode(Node):
    __mapper_args__ = {
        "polymorphic_identity": "topic",
    }


class GraphEdge(Base):
    __tablename__ = "graph_edges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(
        String(64), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target: Mapped[str] = mapped_column(
        String(64), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="edges")
    source_node: Mapped["Node"] = relationship("Node", foreign_keys=[source])
    target_node: Mapped["Node"] = relationship("Node", foreign_keys=[target])
