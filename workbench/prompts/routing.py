import re
from workbench.collaboration.collaboration_entry import route_message

MENTION_PATTERNS = {
    "codex": re.compile(r"(?<![A-Za-z0-9_@\\])@(?:codex|openai)(?![A-Za-z0-9_]|\.[A-Za-z])", re.IGNORECASE),
    "deepseek-harness": re.compile(
        r"(?<![A-Za-z0-9_@\\])@(?:deepseek\s+harness|deepseekharness|deepseek)(?![A-Za-z0-9_]|\.[A-Za-z])",
        re.IGNORECASE,
    ),
    "grok": re.compile(r"(?<![A-Za-z0-9_@\\])@(?:grok-build|grok|xai)(?![A-Za-z0-9_]|\.[A-Za-z])", re.IGNORECASE),
}


ALL_AGENTS_MENTION = re.compile(
    r"(?<![A-Za-z0-9_@\\])@(?:all|everyone|所有|全部)(?![A-Za-z0-9_]|\.[A-Za-z])",
    re.IGNORECASE,
)


CODE_SPAN_PATTERN = re.compile(r"```[\s\S]*?```|`[^`\r\n]*`")


def clean_mention_segment(content: str) -> str:
    for pattern in MENTION_PATTERNS.values():
        content = pattern.sub("", content)
    content = ALL_AGENTS_MENTION.sub("", content)
    content = content.replace(r"\@", "@")
    return re.sub(r"[ \t]+([,，。.!?！？:：;；])", r"\1", content)


def clean_message_prompt(content: str) -> str:
    """Remove routing mentions outside code while preserving code spans."""
    parts = []
    cursor = 0
    for match in CODE_SPAN_PATTERN.finditer(content):
        parts.append(clean_mention_segment(content[cursor:match.start()]))
        parts.append(match.group())
        cursor = match.end()
    parts.append(clean_mention_segment(content[cursor:]))
    return "".join(parts).strip()


def resolve_message_recipients(content: str) -> tuple[list[str], dict[str, str]]:
    """Preserve the whole request; the first mention selects only the entry."""
    recipients, _collaboration = route_message(content)
    return recipients, {agent_id: content for agent_id in recipients}
