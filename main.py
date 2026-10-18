import json
import subprocess
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SESSION_FILE = PROJECT_DIR / "session.json"
ANSWER_FILE = PROJECT_DIR / "last_answer.txt"
SESSIONS_DIR = PROJECT_DIR / "sessions"


def save_transcript(thread_id: str, user_message: str, assistant_message: str) -> None:
    """Append one readable user/assistant exchange to this session's local transcript."""
    safe_thread_id = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in thread_id
    )
    SESSIONS_DIR.mkdir(exist_ok=True)
    transcript = SESSIONS_DIR / f"{safe_thread_id}.md"
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")

    with transcript.open("a", encoding="utf-8") as file:
        if transcript.stat().st_size == 0:
            file.write(f"# Codex 会话记录\n\n- thread_id: `{thread_id}`\n")
        file.write(
            f"\n\n## {timestamp}\n\n"
            f"### 你\n\n{user_message}\n\n"
            f"### Codex\n\n{assistant_message}\n"
        )


def run_codex(prompt: str) -> str:
    user_message = prompt

    if SESSION_FILE.exists():
        session = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        thread_id = session["thread_id"]

        command = [
            "codex", "exec", "resume",
            "--json",
            "--skip-git-repo-check",
            "--output-last-message", str(ANSWER_FILE),
            thread_id,
            prompt,
        ]
    else:
        command = [
            "codex", "exec",
            "--json",
            "--skip-git-repo-check",
            "--output-last-message", str(ANSWER_FILE),
            prompt,
        ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "Codex CLI 执行失败")

    # 第一次运行时，从 JSONL 事件里保存会话 ID。
    if not SESSION_FILE.exists():
        thread_id = None

        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("type") == "thread.started":
                thread_id = event.get("thread_id")
                break

        if not thread_id:
            raise RuntimeError(
                "没有从 Codex 输出中找到会话 ID。"
                "可以检查一下 codex --version 和 codex exec --help。"
            )

        SESSION_FILE.write_text(
            json.dumps({"thread_id": thread_id}, indent=2),
            encoding="utf-8",
        )

    if ANSWER_FILE.exists():
        assistant_message = ANSWER_FILE.read_text(encoding="utf-8").strip()
    else:
        assistant_message = result.stdout.strip()

    save_transcript(thread_id, user_message, assistant_message)
    return assistant_message


print("Codex 对话已启动。输入 :new 新建会话，exit 退出。")

while True:
    prompt = input("\n你：").strip()

    if prompt.lower() in {"exit", "quit"}:
        break

    if prompt.lower() in {":new", "new"}:
        SESSION_FILE.unlink(missing_ok=True)
        print("已切换到新会话。下一条消息会创建新会话。")
        continue

    if not prompt:
        continue

    try:
        print("\nCodex：")
        answer = run_codex(prompt)
        print(answer)
    except Exception as error:
        print(f"\n出错：{error}")
