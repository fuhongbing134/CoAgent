"""Controlled CLI-shaped child. Executed only through the explicit runner."""
import argparse
import json
import sys
import time
from pathlib import Path

FIELDS = ("original_question", "response_or_changes", "evidence", "rejected_alternatives",
          "uncertainties", "expected_action")


def reply(agent, role, marker, bad=False):
    if bad:
        return "invalid fixture protocol"
    item = dict(protocol_version=1, status="completed", questions=[],
                handoff={key: marker for key in FIELDS})
    if role == "entry":
        item["status"] = "continue_work"
        # A separate final responder exercises the final call instead of the
        # workbench's combined owner/final path.
        final_responder = "grok" if agent != "grok" else "codex"
        item["plan"] = dict(owner_id=agent, final_responder_id=final_responder, tasks=[
            dict(task_id="fixture-"+str(i), agent_id=agent,
                 instruction="controlled task "+str(i), depends_on=[]) for i in (1, 2)])
    text = marker + "\n```agent-collaboration\n" + json.dumps(item, ensure_ascii=False) + "\n```"
    return text


def output(adapter, sid, text):
    if adapter == "codex":
        events = [dict(type="thread.started", thread_id=sid)]
    elif adapter == "deepseek-harness":
        events = [dict(type="session", sessionId=sid), dict(type="final", text=text)]
    elif adapter == "grok":
        events = [dict(type="text", data=text), dict(type="end", sessionId=sid, stopReason="end_turn")]
    else:
        raise ValueError("unknown adapter")
    return "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", choices=("codex", "deepseek-harness", "grok"), required=True)
    parser.add_argument("--sid", required=True)
    parser.add_argument("--role", choices=("direct", "entry", "collaborator", "owner", "final"), required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--delay", type=float, choices=(0, .75, 30), required=True)
    parser.add_argument("--fault", choices=("none", "nonzero", "protocol", "timeout", "cancel"), default="none")
    args, cli = parser.parse_known_args()
    # Consume the real worker transport, even though content selection is deterministic.
    if args.adapter == "grok":
        Path(cli[cli.index("--prompt-file")+1]).read_text(encoding="utf-8")
    else:
        sys.stdin.read()
    time.sleep(args.delay)
    if args.fault == "nonzero":
        return 7
    text = reply(args.adapter, args.role, args.marker, args.fault == "protocol") if args.role != "direct" else args.marker
    wire = output(args.adapter, args.sid, text)
    if len(wire.encode("utf-8")) + (len(text.encode("utf-8")) if args.adapter == "codex" else 0) > 4096:
        raise ValueError("fixture output budget exceeded")
    if args.adapter == "codex":
        Path(cli[cli.index("--output-last-message")+1]).write_text(text, encoding="utf-8")
    sys.stdout.buffer.write(wire.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
