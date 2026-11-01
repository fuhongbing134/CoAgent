"""Validated identities for collaboration; importing this module starts no CLI."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

REQUIRED_AGENT_FIELDS = frozenset({"agent_id", "name", "aliases", "adapter"})
OPTIONAL_AGENT_DEFAULTS = {"enabled": True}


@dataclass(frozen=True)
class Agent:
    agent_id: str
    name: str
    aliases: tuple[str, ...]
    adapter: str
    enabled: bool = True


class AgentRegistry:
    def __init__(self, records, *, adapters=("codex", "dsh", "grok")):
        self._agents = {}
        self._names = {}
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("agent record must be an object")
            missing = REQUIRED_AGENT_FIELDS - record.keys()
            unknown = record.keys() - REQUIRED_AGENT_FIELDS - OPTIONAL_AGENT_DEFAULTS.keys()
            if missing or unknown:
                raise ValueError(f"agent record: missing={sorted(missing)}, unknown={sorted(unknown)}")
            record = {**OPTIONAL_AGENT_DEFAULTS, **record}
            agent_id = record["agent_id"]
            if not isinstance(agent_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", agent_id):
                raise ValueError("invalid agent_id")
            if not isinstance(record["name"], str) or not record["name"].strip():
                raise ValueError("name must be nonempty")
            aliases = record["aliases"]
            if not isinstance(aliases, list) or any(not isinstance(a, str) or not a.strip() or "@" in a or "\n" in a for a in aliases):
                raise ValueError("invalid aliases")
            if type(record["enabled"]) is not bool or record["adapter"] not in adapters:
                raise ValueError("invalid enabled/adapter")
            if agent_id in self._agents:
                raise ValueError("duplicate agent_id")
            agent = Agent(agent_id, record["name"], tuple(aliases), record["adapter"], record["enabled"])
            for name in (agent_id, agent.name, *aliases):
                key = name.strip().casefold()
                if "@" in key or "\n" in key or key in {"all", "everyone"}:
                    raise ValueError("reserved/invalid agent name")
                if key in self._names and self._names[key] != agent_id:
                    raise ValueError("ambiguous agent alias")
                self._names[key] = agent_id
            self._agents[agent_id] = agent
        if not self._agents:
            raise ValueError("registry must not be empty")

    @classmethod
    def from_file(cls, path, **kwargs):
        records = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("registry file must contain a list")
        return cls(records, **kwargs)

    def require(self, agent_id):
        if not isinstance(agent_id, str) or agent_id not in self._agents or not self._agents[agent_id].enabled:
            raise ValueError(f"unknown or disabled agent: {agent_id!r}")
        return self._agents[agent_id]

    def resolve(self, name):
        if not isinstance(name, str):
            raise ValueError("agent name must be text")
        key = name.strip().removeprefix("@").casefold()
        if key not in self._names:
            raise ValueError(f"unknown agent name: {name!r}")
        return self.require(self._names[key])

    def enabled_agents(self):
        return tuple(a for a in self._agents.values() if a.enabled)


DEFAULT_AGENTS = [
    dict(agent_id="codex", name="Codex", aliases=["openai"], adapter="codex", enabled=True),
    dict(agent_id="deepseek-harness", name="DeepSeek Harness", aliases=["deepseek", "deepseekharness"], adapter="dsh", enabled=True),
    dict(agent_id="grok", name="Grok", aliases=["grok-build", "xai"], adapter="grok", enabled=True),
]


def default_registry():
    return AgentRegistry(DEFAULT_AGENTS)
