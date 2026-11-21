"""Stage 2: whole-message routing and semantic entry planning contract."""

import json
import re

from workbench.agents.agent_registry import default_registry
from workbench.collaboration.collaboration_protocol import HANDOFF_FIELDS, loads_payload
from workbench.collaboration.collaboration_invitation import terminal_invitation


def route_message(content, registry=None):
    registry = registry or default_registry()
    # Mask fenced/inline code and URLs without changing offsets.
    masked = re.sub(r"```[\s\S]*?(?:```|$)|`[^`\r\n]*`|https?://\S+",
                    lambda m: " " * len(m.group()), content)
    found = []
    broadcast = re.compile(r"(?<![\w@\\])@(?:all|everyone|所有|全部)(?![A-Za-z0-9_])", re.I)
    if broadcast.search(masked):
        return [a.agent_id for a in registry.enabled_agents()], False
    for agent in registry.enabled_agents():
        names = sorted({agent.agent_id, agent.name, *agent.aliases}, key=len, reverse=True)
        pattern = r"(?<![A-Za-z0-9_@\\])@(?:" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z0-9_]|\.[A-Za-z])"
        found.extend((m.start(), agent.agent_id) for m in re.finditer(pattern, masked, re.I))
    if found:
        return [min(found)[1]], True
    return [a.agent_id for a in registry.enabled_agents()], False


def entry_prompt(content, registry=None):
    registry = registry or default_registry()
    example = dict(protocol_version=1, status="continue_work", questions=[],
                   handoff={k: "无／具体内容" for k in sorted(HANDOFF_FIELDS)},
                   plan=dict(owner_id="codex", final_responder_id="codex", tasks=[
                       dict(task_id="task-1", agent_id="codex", instruction="具体任务", depends_on=[])]))
    direct = dict(protocol_version=1, status="completed", questions=[],
                  handoff={k: "无／具体答复" for k in sorted(HANDOFF_FIELDS)},
                  plan=dict(owner_id="codex", final_responder_id="codex", tasks=[]))
    return (
        "你是本次协作的入口。请读取完整用户原文，按语义确定负责人、最终答复者和任务依赖，"
        "不要按提及顺序确定角色。未指定最终答复者时默认最初承接任务者。\n"
        "可用身份：" + json.dumps([dict(id=a.agent_id, name=a.name) for a in registry.enabled_agents()], ensure_ascii=False) + "\n"
        "入口只规划，不执行计划中的修改或命令。所有任务交给调度器执行。\n"
        "有要执行的任务（包括读文件、查数据库、审核、改文件、跑命令等）时返回 continue_work（自任务不需要邀请），"
        "需授权其他 Agent 时返回 request_collaboration（末行独立 @名称）。\n"
        "纯问答、无任何要执行的修改或命令时，直接答复并返回 completed，plan 的 tasks 用空数组 []，不要挂任务。\n"
        "角色有实质歧义时 status=waiting_user，questions 列出问题，plan 可以省略。"
        "blocked 时提供 blocked_reason。有任务时必须提供 plan。\n"
        "工作台负责后续调度：通过本次结果/计划及末行邀请组织协作，不自行通过工具启动其他 Agent。"
        "需要后续协作者时 status=request_collaboration，回复最后单独一行只写 @名称，"
        "否则不要写邀请行。不要声称尚未执行的任务已经完成。\n"
        "可读答复之后输出且仅输出一个 ```agent-collaboration JSON 协议块，结构参考：\n"
        + json.dumps(example, ensure_ascii=False) + "\n纯问答直接答复示例：\n"
        + json.dumps(direct, ensure_ascii=False) + "\n原始用户消息（完整）：\n" + content
    )


def parse_entry_response(text, registry=None, *, role="entry", phase="collaboration"):
    registry = registry or default_registry()
    blocks = list(re.finditer(r"^```agent-collaboration\s*\n(.*?)\n```[ \t]*$", text, re.M | re.S))
    if len(blocks) != 1:
        raise ValueError("expected exactly one agent-collaboration block")
    block = blocks[0]
    tail = text[block.end():].strip()
    targets = terminal_invitation(text, registry)
    if tail:
        if "\n" in tail or not targets:
            raise ValueError("only a terminal invitation may follow protocol")
    payload = loads_payload(block.group(1), registry=registry, role=role, phase=phase, invitation_ids=targets)
    if role == "entry" and payload["status"] not in {"waiting_user", "blocked"} and "plan" not in payload:
        raise ValueError("initial plan required unless asking user or blocked")
    if targets and "plan" in payload:
        tasks = payload.get("plan", {}).get("tasks", [])
        if any(not any(t["agent_id"] == target for t in tasks) for target in targets):
            raise ValueError("invited agent has no task instruction")
    return text[:block.start()].strip(), payload
