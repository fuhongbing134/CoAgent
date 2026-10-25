"""Terminal invitations; names in prose, quoted text and fenced code are inert."""

import re
from agent_registry import default_registry


def terminal_invitation(text, registry=None):
    registry = registry or default_registry()
    lines = text.rstrip().splitlines()
    if not lines:
        return ()
    fence = None
    for line in lines[:-1]:
        match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if match:
            mark = match.group(1)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
    last = lines[-1]
    if fence or last.startswith(("    ", "\t")):
        return ()
    last = last.strip()
    if not last.startswith("@"):
        return ()
    targets = []
    for name in last[1:].split("@"):
        if not name.strip():
            raise ValueError("empty terminal invitation")
        agent_id = registry.resolve(name.strip()).agent_id
        if agent_id not in targets:
            targets.append(agent_id)
    return tuple(targets)
