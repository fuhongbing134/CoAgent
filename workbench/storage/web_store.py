"""SQLite access for the Web workbench."""

import os
import sqlite3
from contextlib import closing
from datetime import datetime
from datetime import timezone
from pathlib import Path

from workbench.execution.execution_tracking import invalidate_sessions
from workbench.execution.migration_barrier import MigrationBarrier
from workbench.storage.database_schema import ensure_schema
from workbench.storage.database_schema import require_current


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _WorkbenchConnection(sqlite3.Connection):
    def close(self):
        try:
            super().close()
        finally:
            permit = getattr(self, '_permit', None)
            if permit is not None:
                self._permit = None
                permit.__exit__(None, None, None)


def connect_workbench(timeout=10, *, DB_PATH):
    permit = MigrationBarrier(DB_PATH)
    permit.__enter__()
    db = None
    try:
        db = sqlite3.connect(Path(DB_PATH).resolve().as_uri() + '?mode=rw', uri=True, timeout=timeout, factory=_WorkbenchConnection)
        db._permit = permit
        require_current(db)
        return db
    except BaseException:
        if db is not None:
            db.close()
        else:
            permit.__exit__(None, None, None)
        raise


def initialize_workbench_tables(*, DB_PATH, connect_workbench) -> None:
    ensure_schema(DB_PATH)
    connection = connect_workbench(timeout=10)
    try:
        with connection:
            connection.execute("""UPDATE workbench_messages SET content=CASE WHEN EXISTS (
                SELECT 1 FROM execution_runs r WHERE r.source_message_id=workbench_messages.id
                AND (r.state IN ('pending','running','quiescing')
                    OR EXISTS (SELECT 1 FROM execution_domains d
                               WHERE d.owner_run_id=r.run_id AND d.state!='idle')
                    OR EXISTS (SELECT 1 FROM execution_sessions s WHERE s.owner_run_id=r.run_id))
                ) THEN ? ELSE ? END, status='cancelled' WHERE status='pending'""",
                ('工作台曾中断；此任务未完成收尾，旧执行退出情况未知，相关占用仍保留。',
                 '工作台曾中断；此消息已取消，旧执行退出情况未知，未确认关联占用。'))
    finally:
        connection.close()


def open_database(*, DB_PATH) -> sqlite3.Connection:
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"找不到桥接数据库：{DB_PATH}")
    connection = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def get_working_directory_text(conversation_id: str, *, PROJECT_DIR, open_database) -> str:
    with closing(open_database()) as connection:
        row = connection.execute(
            "SELECT working_directory FROM workbench_conversation_settings WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    return row["working_directory"] if row else str(PROJECT_DIR)


def resolve_working_directory(conversation_id: str, *, get_working_directory_text) -> Path:
    configured = Path(get_working_directory_text(conversation_id)).expanduser()
    try:
        resolved = configured.resolve(strict=True)
    except OSError as error:
        raise FileNotFoundError(f"工作目录不存在或无法访问：{configured}") from error
    if not resolved.is_dir():
        raise NotADirectoryError(f"工作目录不是文件夹：{resolved}")
    return resolved


def normalize_working_directory(value: str) -> str:
    value = os.path.expandvars(value.strip().strip('"'))
    if not value:
        raise ValueError("请输入工作目录的绝对路径")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ValueError("工作目录必须使用绝对路径")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError(f"工作目录不存在或无法访问：{candidate}") from error
    if not resolved.is_dir():
        raise ValueError(f"指定路径不是文件夹：{resolved}")
    return str(resolved)


def save_working_directory(conversation_id: str, working_directory: str, *, PROJECT_DIR, SESSION_CONFIGURATION_RESOLVER, connect_workbench) -> bool:
    # Settings holds the short registry gate: fail fast on DB contention.
    connection = connect_workbench(timeout=0)
    try:
        with connection:
            row = connection.execute(
                "SELECT working_directory FROM workbench_conversation_settings WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            old_value = row[0] if row else str(PROJECT_DIR)
            if old_value == working_directory:
                return False
            connection.execute(
                """INSERT INTO workbench_conversation_settings
                   (conversation_id, working_directory, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(conversation_id) DO UPDATE SET
                       working_directory = excluded.working_directory,
                       updated_at = excluded.updated_at""",
                (conversation_id, working_directory, utc_now()),
            )
            # CLI sessions are tied to the directory where they were created.
            # Keep the transcript; enabled task-5 bindings require explicit validation/adoption.
            if SESSION_CONFIGURATION_RESOLVER is not None:
                invalidate_sessions(connection, conversation_id)
            else:
                # Pre-cutover 4B compatibility only; never used by the enabled task-5 path.
                connection.execute("DELETE FROM workbench_sessions WHERE conversation_id = ?", (conversation_id,))
                connection.execute("DELETE FROM workbench_agent_sessions WHERE conversation_id = ?", (conversation_id,))
            return True
    finally:
        connection.close()


def fetch_agents(*, open_database) -> list[dict]:
    with closing(open_database()) as connection:
        rows = connection.execute(
            """SELECT agent_id, name, vendor, model, role, registered_at, last_seen
               FROM agents ORDER BY registered_at, agent_id"""
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_conversations(*, open_database) -> list[dict]:
    with closing(open_database()) as connection:
        bridge_rows = connection.execute(
            """SELECT conversation_id, COUNT(*) AS message_count, MAX(created_at) AS latest_at
               FROM messages GROUP BY conversation_id"""
        ).fetchall()
        workbench_rows = connection.execute(
            """SELECT conversation_id, COUNT(*) AS message_count, MAX(created_at) AS latest_at
               FROM workbench_messages GROUP BY conversation_id"""
        ).fetchall()
    conversations: dict[str, dict] = {}
    for row in [*bridge_rows, *workbench_rows]:
        item = conversations.setdefault(row["conversation_id"], {
            "conversation_id": row["conversation_id"],
            "message_count": 0,
            "latest_at": "",
        })
        item["message_count"] += row["message_count"]
        item["latest_at"] = max(item["latest_at"], row["latest_at"] or "")
    return sorted(conversations.values(), key=lambda item: item["latest_at"], reverse=True)


def fetch_messages(conversation_id: str, limit: int, *, open_database) -> list[dict]:
    with closing(open_database()) as connection:
        bridge_rows = connection.execute(
            """SELECT m.id, m.conversation_id, m.sender_id,
                      COALESCE(sender.name, m.sender_id) AS sender_name,
                      m.recipient_id,
                      COALESCE(recipient.name, m.recipient_id) AS recipient_name,
                      m.content, m.created_at, 'completed' AS status, 'bridge' AS source
               FROM messages AS m
               LEFT JOIN agents AS sender ON sender.agent_id = m.sender_id
               LEFT JOIN agents AS recipient ON recipient.agent_id = m.recipient_id
               WHERE m.conversation_id = ?
               ORDER BY m.id DESC LIMIT ?""",
            (conversation_id, limit),
        ).fetchall()
        workbench_rows = connection.execute(
            """SELECT m.id, m.conversation_id, m.sender_id,
                      CASE WHEN m.sender_id = 'user' THEN '你'
                           ELSE COALESCE(sender.name, m.sender_id) END AS sender_name,
                      m.recipient_id,
                      CASE WHEN m.recipient_id = 'user' THEN '你'
                           WHEN m.recipient_id = 'all' THEN '所有 Agent'
                           ELSE COALESCE(recipient.name, m.recipient_id) END AS recipient_name,
                      m.content, m.created_at, m.status, 'workbench' AS source
               FROM workbench_messages AS m
               LEFT JOIN agents AS sender ON sender.agent_id = m.sender_id
               LEFT JOIN agents AS recipient ON recipient.agent_id = m.recipient_id
               WHERE m.conversation_id = ?
               ORDER BY m.id DESC LIMIT ?""",
            (conversation_id, limit),
        ).fetchall()
    messages = [dict(row) for row in bridge_rows]
    messages.extend(dict(row) for row in workbench_rows)
    messages.sort(key=lambda item: (item["created_at"], item["source"], item["id"]))
    return messages[-limit:]


def get_codex_thread_id(conversation_id: str, *, open_database) -> str | None:
    with closing(open_database()) as connection:
        row = connection.execute(
            "SELECT codex_thread_id FROM workbench_sessions WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    return row["codex_thread_id"] if row else None


def save_codex_thread_id(conversation_id: str, thread_id: str, *, SESSION_CONFIGURATION_RESOLVER, connect_workbench) -> None:
    if SESSION_CONFIGURATION_RESOLVER is not None:
        raise RuntimeError('Legacy session storage is read-only after task-5 cutover')
    connection = connect_workbench(timeout=10)
    try:
        with connection:
            connection.execute(
                """INSERT INTO workbench_sessions (conversation_id, codex_thread_id, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(conversation_id) DO UPDATE SET
                       codex_thread_id = excluded.codex_thread_id,
                       updated_at = excluded.updated_at""",
                (conversation_id, thread_id, utc_now()),
            )
    finally:
        connection.close()


def get_agent_session_id(conversation_id: str, agent_id: str, *, open_database) -> str | None:
    with closing(open_database()) as connection:
        row = connection.execute(
            """SELECT session_id FROM workbench_agent_sessions
               WHERE conversation_id = ? AND agent_id = ?""",
            (conversation_id, agent_id),
        ).fetchone()
    return row["session_id"] if row else None


def save_agent_session_id(conversation_id: str, agent_id: str, session_id: str, *, SESSION_CONFIGURATION_RESOLVER, connect_workbench) -> None:
    if SESSION_CONFIGURATION_RESOLVER is not None:
        raise RuntimeError('Legacy session storage is read-only after task-5 cutover')
    connection = connect_workbench(timeout=10)
    try:
        with connection:
            connection.execute(
                """INSERT INTO workbench_agent_sessions
                   (conversation_id, agent_id, session_id, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(conversation_id, agent_id) DO UPDATE SET
                       session_id = excluded.session_id,
                       updated_at = excluded.updated_at""",
                (conversation_id, agent_id, session_id, utc_now()),
            )
    finally:
        connection.close()


def update_workbench_message(message_id: int, content: str, status: str, *, COLLAB_CONTEXT, connect_workbench) -> None:
    validating = getattr(COLLAB_CONTEXT, "validating_entry", False)
    received = status == "completed"
    if validating:
        COLLAB_CONTEXT.entry_reply_received = False
        if received:
            # Use the existing durable state; transport success is tracked
            # separately so an untouched pending row cannot be accepted.
            status = "pending"
    connection = connect_workbench(timeout=10)
    try:
        with connection:
            cursor = connection.execute(
                "UPDATE workbench_messages SET content = ?, status = ?, created_at = ? WHERE id = ?"
                + " AND status='pending'",
                (content, status, utc_now(), message_id),
            )
        if validating:
            COLLAB_CONTEXT.entry_reply_received = received and cursor.rowcount == 1
    finally:
        connection.close()


def stage_collaboration_reply(message_id, job_id, call_id, text, *, connect_workbench):
    with closing(connect_workbench()) as db, db:
        changed = db.execute("""UPDATE workbench_messages SET content=? WHERE id=? AND status='pending'
            AND EXISTS (SELECT 1 FROM execution_runs r WHERE r.source_message_id=workbench_messages.id
                        AND r.source_job_id=? AND r.source_call_id=?)""",
            (text, message_id, job_id, call_id)).rowcount
        if not changed:
            row = db.execute('SELECT status FROM workbench_messages WHERE id=?', (message_id,)).fetchone()
            if not row or row[0] != 'cancelled':
                raise RuntimeError('Collaboration staging association/state mismatch')


def finalize_message(db, *, message_id, job_id, call_id, disposition, reason=''):
    """Participate in the caller's reception transaction; never commit here."""
    row = db.execute("""SELECT m.status,m.content FROM workbench_messages m
        JOIN execution_runs r ON r.source_message_id=m.id
        JOIN collab_calls c ON c.job_id=r.source_job_id AND c.call_id=r.source_call_id
        WHERE m.id=? AND r.source_job_id=? AND r.source_call_id=?
          AND m.sender_id=c.agent_id""", (message_id,job_id,call_id)).fetchone()
    if not row:
        raise RuntimeError('Message/call association mismatch')
    if disposition == 'inspect':
        return row[0]
    if disposition == 'duplicate' or row[0] != 'pending':
        return  # Preserve cancellation and every established terminal state.
    status = {'accepted':'completed', 'rejected':'failed',
              'cancelled':'cancelled', 'superseded':'cancelled'}[disposition]
    # Superseded is an obsolete reply, not a protocol failure; existing cancelled
    # presentation avoids adding a schema/protocol status or leaving pending.
    suffix = ('\n[协作回复未接收] ' + reason if disposition == 'rejected' else
              '\n[回复已失效] ' + reason if disposition == 'superseded' else '')
    db.execute("UPDATE workbench_messages SET content=?,status=? WHERE id=? AND status='pending'",
               (row[1] + suffix,status,message_id))


def touch_agent_last_seen(agent_id: str, *, connect_workbench) -> None:
    connection = connect_workbench(timeout=10)
    try:
        with connection:
            connection.execute(
                "UPDATE agents SET last_seen = ? WHERE agent_id = ?",
                (utc_now(), agent_id),
            )
    finally:
        connection.close()
