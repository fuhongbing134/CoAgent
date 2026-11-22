"""Conservative Grok NDJSON boundary; no CLI, configuration or DB side effects."""
import json
import re


def diagnostic(text):
    text = re.sub(r'https?://\S+', '[url]', str(text))
    text = re.sub(r'(?i)(bearer\s+|(?:api[_-]?key|token|password|secret)\s*[=:]\s*)\S+',
                  r'\1[redacted]', text)
    return text[:500]


def parse_output(stdout):
    texts, errors, notes, ids, reasons = [], [], [], set(), []
    ended = False
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            if len(notes) < 10:
                notes.append(diagnostic(line))
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get('type')
        if kind == 'text' and isinstance(event.get('data'), str):
            texts.append(event['data'])
        elif kind == 'end':
            ended = True
            sid = event.get('sessionId')
            if isinstance(sid, str) and sid.strip() and all(c.isalnum() or c in '._-' for c in sid):
                ids.add(sid)
            else:
                errors.append('Invalid Grok session ID')
            reasons.append(event.get('stopReason'))
        elif kind == 'error':
            errors.append(diagnostic(event.get('message') or 'Grok CLI error'))
    if not errors and (not ended or not reasons or any(r not in ('end_turn', 'EndTurn') for r in reasons)):
        errors.append('Grok normal completion evidence missing or unsupported stopReason')
    if not errors and len(ids) != 1:
        errors.append('Grok session ID missing or conflicting')
    return ''.join(texts).strip(), next(iter(ids)) if len(ids) == 1 else None, errors, notes


def command(executable, prompt_path, cwd, session_id, final_delivery, *,
            file_profile=None, allow_git=False):
    args = [executable, '--prompt-file', str(prompt_path), '--output-format', 'streaming-json',
            '--cwd', str(cwd), '--permission-mode', 'dontAsk']
    if not final_delivery:
        args += ['--allow', 'Read', '--allow', 'Grep']
        if allow_git:
            args += ['--allow', 'Bash(git *)']
        if file_profile:
            args += ['--agent', str(file_profile), '--allow', 'MCPTool(workbench_files__*)']
    args += ['--no-subagents']
    if session_id:
        args += ['--resume', session_id]
    if final_delivery:
        args += ['--deny', '*', '--disable-web-search']
    return args


def requested_recipients(content_targets, requested, collaboration):
    """A client may narrow a broadcast, never redirect a targeted request."""
    if requested is None:
        return content_targets
    if (not isinstance(requested, list) or not requested
            or any(not isinstance(x, str) for x in requested)
            or len(set(requested)) != len(requested)
            or not set(requested) <= set(content_targets)
            or (collaboration and requested != content_targets)):
        raise ValueError('Invalid recipients: refresh availability and retry')
    return [x for x in content_targets if x in requested]
