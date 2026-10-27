"""A small local MCP message bridge for Codex and DeepSeek Harness."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from database_schema import ensure_schema, require_current
from migration_barrier import MigrationBarrier
from mcp.server.fastmcp import FastMCP


PROJECT_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("AGENT_BRIDGE_DB", PROJECT_DIR / "agent_bridge.db")).expanduser().resolve()
AGENT_ID = os.environ.get("AGENT_BRIDGE_AGENT_ID", "unknown-agent")
MAX_MESSAGE_LENGTH = 20_000

mcp = FastMCP("agent-bridge")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    with MigrationBarrier(DB_PATH):
        connection = connect_db()
        try:
            require_current(connection)
            with connection:
                yield connection
        finally:
            connection.close()


def initialize_db(*, allow_legacy=False) -> None:
    ensure_schema(DB_PATH, allow_legacy=allow_legacy)


def touch_agent(connection: sqlite3.Connection) -> None:
    connection.execute(
        "UPDATE agents SET last_seen = ? WHERE agent_id = ?",
        (utc_now(), AGENT_ID),
    )


@mcp.tool()
def register_agent(name: str, vendor: str, model: str, role: str) -> dict:
    """Register this MCP client before using the bridge; later calls refresh last_seen."""
    now = utc_now()
    with database() as connection:
        connection.execute(
            """INSERT INTO agents
               (agent_id, name, vendor, model, role, registered_at, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(agent_id) DO UPDATE SET
                   name = excluded.name,
                   vendor = excluded.vendor,
                   model = excluded.model,
                   role = excluded.role,
                   last_seen = excluded.last_seen""",
            (AGENT_ID, name, vendor, model, role, now, now),
        )
    return {"agent_id": AGENT_ID, "registered": True, "last_seen": now}


@mcp.tool()
def list_agents() -> list[dict]:
    """List agents that have registered with this shared message bridge."""
    with database() as connection:
        touch_agent(connection)
        rows = connection.execute(
            """SELECT agent_id, name, vendor, model, role, registered_at, last_seen
               FROM agents ORDER BY registered_at, agent_id"""
        ).fetchall()
    return [dict(row) for row in rows]


@mcp.tool()
def send_message(recipient_id: str, content: str, conversation_id: str = "default") -> dict:
    """Send a message to another registered agent in a shared conversation."""
    content = content.strip()
    conversation_id = conversation_id.strip()
    if not content:
        raise ValueError("消息内容不能为空。")
    if len(content) > MAX_MESSAGE_LENGTH:
        raise ValueError(f"消息不能超过 {MAX_MESSAGE_LENGTH} 个字符。")
    if not conversation_id:
        raise ValueError("conversation_id 不能为空。")
    if recipient_id == AGENT_ID:
        raise ValueError("不能给自己发送消息。")

    now = utc_now()
    with database() as connection:
        touch_agent(connection)
        recipient = connection.execute(
            "SELECT 1 FROM agents WHERE agent_id = ?", (recipient_id,)
        ).fetchone()
        if recipient is None:
            raise ValueError(f"接收方 {recipient_id!r} 尚未注册。先调用 list_agents 查看可用 Agent。")
        cursor = connection.execute(
            """INSERT INTO messages
               (conversation_id, sender_id, recipient_id, content, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (conversation_id, AGENT_ID, recipient_id, content, now),
        )
        message_id = cursor.lastrowid
    return {
        "message_id": message_id,
        "conversation_id": conversation_id,
        "from": AGENT_ID,
        "to": recipient_id,
        "created_at": now,
    }


@mcp.tool()
def read_inbox(
    conversation_id: str = "default",
    after_id: int = 0,
    limit: int = 50,
) -> list[dict]:
    """Read messages addressed to this agent; read messages are marked as read."""
    limit = max(1, min(limit, 200))
    with database() as connection:
        touch_agent(connection)
        rows = connection.execute(
            """SELECT id, conversation_id, sender_id, recipient_id, content, created_at, read_at
               FROM messages
               WHERE recipient_id = ? AND conversation_id = ? AND id > ?
               ORDER BY id LIMIT ?""",
            (AGENT_ID, conversation_id, max(0, after_id), limit),
        ).fetchall()
        if rows:
            ids = [row["id"] for row in rows]
            placeholders = ",".join("?" for _ in ids)
            connection.execute(
                f"UPDATE messages SET read_at = ? WHERE id IN ({placeholders}) AND read_at IS NULL",
                (utc_now(), *ids),
            )
    return [dict(row) for row in rows]


@mcp.tool()
def read_conversation(conversation_id: str = "default", limit: int = 100) -> list[dict]:
    """Read recent conversation messages in threads where this agent participates."""
    limit = max(1, min(limit, 200))
    with database() as connection:
        touch_agent(connection)
        rows = connection.execute(
            """SELECT id, conversation_id, sender_id, recipient_id, content, created_at, read_at
               FROM messages
               WHERE conversation_id = ? AND (sender_id = ? OR recipient_id = ?)
               ORDER BY id DESC LIMIT ?""",
            (conversation_id, AGENT_ID, AGENT_ID, limit),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-legacy-upgrade', action='store_true', help='Confirm all legacy writers are stopped')
    args = parser.parse_args()
    initialize_db(allow_legacy=args.allow_legacy_upgrade)
    mcp.run(transport="stdio")
