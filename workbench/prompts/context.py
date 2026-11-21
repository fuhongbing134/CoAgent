from contextlib import closing
from pathlib import Path

def truncate_head_tail(content: str, limit: int) -> str:
    """Keep the head and tail of an over-long message and mark the omitted middle.

    ``limit`` counts the kept body characters only; the omission note is added on top.
    """
    total = len(content)
    head_len = limit // 2
    tail_len = limit - head_len  # odd limit gives the tail one extra character
    omitted = total - limit
    return (
        content[:head_len]
        + f"\n…（中间省略 {omitted} 字符；原文共 {total} 字符）\n"
        + content[-tail_len:]
    )


def build_agent_prompt(
    conversation_id: str,
    agent_id: str,
    user_message_id: int,
    prompt: str,
    working_directory: Path,
    *, open_database, max_messages: int, max_message_chars: int,
) -> str:
    """Add shared context and the active workspace policy to the agent request."""
    with closing(open_database()) as connection:
        workbench_rows = connection.execute(
            """SELECT m.sender_id, COALESCE(sender.name, m.sender_id) AS sender_name,
                      m.recipient_id, COALESCE(recipient.name, m.recipient_id) AS recipient_name,
                      m.content, m.created_at, m.id, 'workbench' AS source
               FROM workbench_messages AS m
               LEFT JOIN agents AS sender ON sender.agent_id = m.sender_id
               LEFT JOIN agents AS recipient ON recipient.agent_id = m.recipient_id
               WHERE m.conversation_id = ? AND m.id < ? AND m.status = 'completed'
               ORDER BY m.id DESC LIMIT ?""",
            (conversation_id, user_message_id, max_messages),
        ).fetchall()
        bridge_rows = connection.execute(
            """SELECT m.sender_id, COALESCE(sender.name, m.sender_id) AS sender_name,
                      m.recipient_id, COALESCE(recipient.name, m.recipient_id) AS recipient_name,
                      m.content, m.created_at, m.id, 'bridge' AS source
               FROM messages AS m
               LEFT JOIN agents AS sender ON sender.agent_id = m.sender_id
               LEFT JOIN agents AS recipient ON recipient.agent_id = m.recipient_id
               WHERE m.conversation_id = ?
               ORDER BY m.id DESC LIMIT ?""",
            (conversation_id, max_messages),
        ).fetchall()

    history = [dict(row) for row in workbench_rows]
    history.extend(dict(row) for row in bridge_rows)
    history = [
        item for item in history
        if item["source"] == "bridge"
        or (item["sender_id"] != agent_id
            and not (item["sender_id"] == "user"
                     and item["recipient_id"] in {agent_id, "all"}))
    ]
    history.sort(key=lambda item: (item["created_at"], item["source"], item["id"]))
    history = history[-max_messages:]
    transcript = []
    for item in history:
        content = item["content"]
        if len(content) > max_message_chars:
            content = truncate_head_tail(content, max_message_chars)
        transcript.append(
            f"[{item['sender_name']} → {item['recipient_name']}]\n{content}"
        )
    shared_context = ""
    if transcript:
        shared_context = (
            "以下是当前工作台共享对话中其他 Agent 或消息桥的近期上下文。"
            "请将其作为背景，不要重复回答历史问题。\n\n"
            + "\n\n".join(transcript)
            + "\n\n"
        )
    workspace_context = f"当前工作目录：{working_directory}\n\n"
    project_entry = (working_directory / "AGENTS.md").read_text(encoding="utf-8")
    entry_context = (
        "以下是项目入口（来源：AGENTS.md 全文）\n"
        f"入口所在项目目录：{working_directory}\n\n"
        + project_entry
        + "\n\n"
    )
    spec_index = (working_directory / "specs" / "README.md").read_text(encoding="utf-8")
    spec_context = (
        "以下是项目规范索引（来源：specs/README.md 全文）\n"
        f"索引所在项目目录：{working_directory}\n"
        "索引内列出的规范路径均相对于该项目的 specs/ 目录，"
        "如 AGENT_DISCUSSION_SPEC.md 对应 specs/AGENT_DISCUSSION_SPEC.md。\n"
        "操作前按适用场景判断规范归属（must_follow/not_applicable/uncertain）"
        "并读取 must_follow 规范正文；场景变化时复核；所需正文不可用时补读；"
        "读取失败如实说明；交付时在 handoff.evidence 简要记录适用规范 id、"
        "版本、路径、读取情况与失败项（普通聊天无 handoff 时在可读答复中简记）。\n\n"
        + spec_index
        + "\n\n"
    )
    return entry_context + spec_context + workspace_context + shared_context + "当前用户消息：\n" + prompt
