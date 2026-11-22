"""Workbench CLI adapters."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from workbench.adapters.deepseek_command import build_dsh_command
from workbench.adapters.grok_adapter import command as grok_command_args
from workbench.adapters.grok_adapter import diagnostic as grok_diagnostic
from workbench.adapters.grok_adapter import parse_output as grok_parse_output
from workbench.adapters.grok_diagnostics import capture as capture_grok_diagnostic
from workbench.execution.execution_runtime import format_execution_error
from workbench.execution.web_runs import AgentRunCancelled


def resolve_dsh_command() -> str | None:
    configured = os.environ.get("DSH_CLI", "dsh").strip().strip('"')
    if not configured:
        return None
    located = shutil.which(configured)
    if located:
        return located
    candidate = Path(configured).expanduser()
    if candidate.is_file():
        return str(candidate)
    return None


def resolve_grok_command() -> str | None:
    configured = os.environ.get("GROK_CLI", "").strip().strip('"')
    if configured:
        located = shutil.which(configured)
        if located:
            return located
        candidate = Path(configured).expanduser()
        return str(candidate) if candidate.is_file() else None
    located = shutil.which("grok")
    if located:
        return located
    candidate = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".grok" / "bin" / ("grok.exe" if os.name == "nt" else "grok")
    return str(candidate) if candidate.is_file() else None


def resolve_codex_command() -> str | None:
    configured = os.environ.get("CODEX_CLI", "").strip().strip('"')
    if configured:
        located = shutil.which(configured)
        if located:
            return located
        candidate = Path(configured).expanduser()
        return str(candidate) if candidate.is_file() else None

    located = shutil.which("codex")
    if located:
        return located

    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    codex_bin = local_app_data / "OpenAI" / "Codex" / "bin"
    candidates = list(codex_bin.glob("*/codex.exe")) if codex_bin.is_dir() else []
    if not candidates:
        return None
    candidates.sort(key=lambda candidate: candidate.stat().st_mtime, reverse=True)
    return str(candidates[0])


def run_codex(
    conversation_id: str,
    prompt: str,
    assistant_message_id: int,
    user_message_id: int,
    *,
    AGENT_CODEX_TIMEOUT_SECONDS,
    CODEX_SANDBOX,
    COLLAB_CONTEXT,
    active_run,
    build_agent_prompt,
    is_run_cancelled,
    prepare_execution_session,
    resolve_codex_command,
    run_managed_process,
    save_codex_thread_id,
    touch_agent_last_seen,
    worker_reply,
) -> WorkerResult:
    try:
        touch_agent_last_seen("codex")
        working_directory = active_run(assistant_message_id)['execution'].working_directory
        codex_command = resolve_codex_command()
        if not codex_command:
            raise RuntimeError(
                "找不到 Codex CLI。请确认已安装 Codex；也可以设置 CODEX_CLI 为 codex.exe 的完整路径。"
            )

        environment = os.environ.copy()
        thread_id = prepare_execution_session(assistant_message_id, 'codex', codex_command, environment)
        agent_prompt = build_agent_prompt(
            conversation_id, "codex", user_message_id, prompt, working_directory
        )
        with tempfile.TemporaryDirectory(prefix="agent-workbench-") as temporary_directory:
            answer_path = Path(temporary_directory) / "codex-answer.txt"
            requested_profile = None
            if getattr(COLLAB_CONTEXT, "final_delivery", False):
                permission_args = ["--sandbox", "read-only"]
            elif working_directory.resolve() == Path(
                "C:/Users/FHB/Documents/Codex/agent-lab/step1"
            ).resolve():
                requested_profile = "step1-git"
                permission_args = ["-c", 'default_permissions="step1-git"']
            else:
                permission_args = ["--sandbox", CODEX_SANDBOX]

            command = [codex_command, "exec", *permission_args, "--cd", str(working_directory)]
            if thread_id:
                command.append("resume")
            command.extend([
                "--json", "--skip-git-repo-check", "--output-last-message", str(answer_path),
            ])
            if thread_id:
                command.append(thread_id)
            command.append("-")

            result = run_managed_process(
                assistant_message_id,
                command,
                cwd=working_directory,
                env=environment,
                input=agent_prompt,
                diagnostic_metadata={
                    "agent_id": "codex",
                    "permission_profile_requested": requested_profile,
                    "transport": "stdin",
                    "session_mode": "resume" if thread_id else "new",
                    "prompt_utf8_bytes": len(agent_prompt.encode("utf-8")),
                    # Windows serialization length in UTF-16 units, excluding NUL.
                    "command_utf16_units": len(
                        subprocess.list2cmdline(command).encode("utf-16-le")
                    ) // 2,
                },
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=AGENT_CODEX_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                diagnostic = (result.stderr or result.stdout or "Codex CLI 执行失败").strip()
                raise RuntimeError(diagnostic)

            if not thread_id:
                for line in result.stdout.splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "thread.started":
                        thread_id = event.get("thread_id")
                        break
                if not thread_id:
                    raise RuntimeError("Codex 已返回，但没有找到新会话的 thread_id。")
                if active_run(assistant_message_id)['execution'].session_claim is None:
                    save_codex_thread_id(conversation_id, thread_id)

            answer = answer_path.read_text(encoding="utf-8").strip() if answer_path.exists() else ""
            if not answer:
                raise RuntimeError("Codex 没有返回可显示的最终回复。")
            return worker_reply(assistant_message_id, answer, "completed")
    except AgentRunCancelled:
        return worker_reply(assistant_message_id, "已停止。", "cancelled")
    except subprocess.TimeoutExpired:
        if is_run_cancelled(assistant_message_id):
            return worker_reply(assistant_message_id, "已停止。", "cancelled")
        else:
            return worker_reply(assistant_message_id, f"Codex 执行超时（{AGENT_CODEX_TIMEOUT_SECONDS} 秒）。可能已有文件修改，未自动回退。", "failed")
    except Exception as error:
        if is_run_cancelled(assistant_message_id):
            return worker_reply(assistant_message_id, "已停止。", "cancelled")
        else:
            message = format_execution_error(error)
            return worker_reply(assistant_message_id, "调用 Codex 失败：" + message[:4000], "failed")


def run_deepseek_harness(
    conversation_id: str,
    prompt: str,
    assistant_message_id: int,
    user_message_id: int,
    *,
    AGENT_DEEPSEEK_TIMEOUT_SECONDS,
    DSH_AUTH_PATCH,
    DSH_PROFILE,
    DSH_SESSION_KEY,
    active_run,
    build_agent_prompt,
    is_run_cancelled,
    prepare_execution_session,
    resolve_dsh_command,
    run_managed_process,
    save_agent_session_id,
    touch_agent_last_seen,
    worker_reply,
) -> WorkerResult:
    try:
        touch_agent_last_seen("deepseek-harness")
        working_directory = active_run(assistant_message_id)['execution'].working_directory
        dsh_command = resolve_dsh_command()
        if not dsh_command:
            raise RuntimeError(
                "找不到 dsh CLI。请在 DeepSeek Harness 桌面端的“Manage dsh Command”中安装命令，"
                "重新打开 PowerShell 并重启工作台；也可设置 DSH_CLI 为 dsh 可执行文件的完整路径。"
            )
        if not DSH_AUTH_PATCH.is_file():
            raise RuntimeError(f"找不到 DSH headless 登录配置：{DSH_AUTH_PATCH}")

        # Build the launch configuration first; resolve its fixed ID exactly once below.
        agent_prompt = build_agent_prompt(
            conversation_id, "deepseek-harness", user_message_id, prompt, working_directory
        )
        arguments = [
            "--profile", DSH_PROFILE,
            "--patch", str(DSH_AUTH_PATCH),
            "--json",
        ]
        arguments.append("-")
        command, use_shell, use_electron_as_node = build_dsh_command(dsh_command, arguments)
        environment = os.environ.copy()
        if use_electron_as_node:
            environment["ELECTRON_RUN_AS_NODE"] = "1"
        session_id = prepare_execution_session(assistant_message_id, 'deepseek-harness', command[0], environment)
        if session_id:
            if not all(char.isalnum() or char in '._-' for char in session_id):
                raise RuntimeError('Invalid DSH resume ID')
            command[-1:-1] = ['--session-id', session_id]
        result = run_managed_process(
            assistant_message_id,
            command,
            cwd=working_directory,
            env=environment,
            input=agent_prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=AGENT_DEEPSEEK_TIMEOUT_SECONDS,
            shell=use_shell,
        )

        events: list[dict] = []
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)

        if not session_id:
            for event in events:
                if event.get("type") == "session" and isinstance(event.get("sessionId"), str):
                    session_id = event["sessionId"]
                    break
            if session_id:
                if active_run(assistant_message_id)['execution'].session_claim is None:
                    save_agent_session_id(conversation_id, DSH_SESSION_KEY, session_id)

        if result.returncode != 0:
            diagnostic = (result.stderr or "").strip()
            error_events = [event.get("message") for event in events if event.get("type") == "error"]
            diagnostic = diagnostic or next((str(message) for message in error_events if message), "DSH CLI 执行失败")
            raise RuntimeError(diagnostic[:4000])

        answer = next(
            (event.get("text", "") for event in reversed(events) if event.get("type") == "final"),
            "",
        ).strip()
        if not answer:
            raise RuntimeError("DeepSeek Harness 已退出，但没有返回可显示的最终回复。")
        return worker_reply(assistant_message_id, answer, "completed")
    except AgentRunCancelled:
        return worker_reply(assistant_message_id, "已停止。", "cancelled")
    except subprocess.TimeoutExpired:
        if is_run_cancelled(assistant_message_id):
            return worker_reply(assistant_message_id, "已停止。", "cancelled")
        else:
            return worker_reply(assistant_message_id, f"DeepSeek Harness 执行超时（{AGENT_DEEPSEEK_TIMEOUT_SECONDS} 秒）。可能已有文件修改，未自动回退。", "failed")
    except Exception as error:
        if is_run_cancelled(assistant_message_id):
            return worker_reply(assistant_message_id, "已停止。", "cancelled")
        else:
            message = format_execution_error(error)
            return worker_reply(assistant_message_id, "调用 DeepSeek Harness 失败：" + message[:4000], "failed")


def build_grok_command(grok_command: str, prompt_path: Path, working_directory: Path,
                       session_id: str | None, final_delivery: bool, *,
                       file_profile=None, allow_git=False) -> list[str]:
    """Explicit tool approvals; final denies tools, not a Windows OS sandbox."""
    return grok_command_args(grok_command, prompt_path, working_directory, session_id, final_delivery,
                             file_profile=file_profile, allow_git=allow_git)


def grok_test_git_allowed(root: Path) -> bool:
    if root != Path(__file__).resolve().parents[2]:
        return False
    try:
        branch = subprocess.run(
            ['git', 'symbolic-ref', '--quiet', '--short', 'HEAD'], cwd=root,
            capture_output=True, text=True, timeout=5, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return branch.stdout.strip() == 'feature/GitDiscussion'


def parse_grok_output(stdout: str) -> tuple[str, str | None, list[str], list[str]]:
    return grok_parse_output(stdout)


def run_grok(
    conversation_id: str,
    prompt: str,
    assistant_message_id: int,
    user_message_id: int,
    *,
    AGENT_GROK_TIMEOUT_SECONDS,
    COLLAB_CONTEXT,
    GROK_SESSION_KEY,
    active_run,
    build_agent_prompt,
    build_grok_command,
    is_run_cancelled,
    parse_grok_output,
    prepare_execution_session,
    resolve_grok_command,
    run_managed_process,
    save_agent_session_id,
    touch_agent_last_seen,
    worker_reply,
) -> WorkerResult:
    try:
        touch_agent_last_seen("grok")
        run = active_run(assistant_message_id)
        execution = run['execution']
        working_directory = execution.working_directory
        grok_command = resolve_grok_command()
        if not grok_command:
            raise RuntimeError(
                "找不到 Grok CLI。请安装 Grok Build（grok）并登录；也可设置 GROK_CLI 为 grok.exe 的完整路径。"
            )
        agent_prompt = build_agent_prompt(
            conversation_id, "grok", user_message_id, prompt, working_directory
        )
        environment = os.environ.copy()
        session_id = prepare_execution_session(assistant_message_id, 'grok', grok_command, environment)
        if session_id and not all(char.isalnum() or char in '._-' for char in session_id):
            raise RuntimeError('Invalid Grok resume ID')
        final_delivery = getattr(COLLAB_CONTEXT, "final_delivery", False)
        diagnostic_enabled = run.get('grok_diagnostic', False) and not final_delivery and not session_id
        with (
            tempfile.TemporaryDirectory(prefix="agent-workbench-") as temporary_directory,
            capture_grok_diagnostic(execution, Path(temporary_directory), diagnostic_enabled) as evidence,
        ):
            prompt_path = Path(temporary_directory) / "grok-prompt.txt"
            file_options = {}
            if not final_delivery:
                root = Path(working_directory).resolve(strict=True)
                profile = prompt_path.with_name('workbench-files.md')
                server = {'command': sys.executable, 'args': [
                    '-X', 'utf8', str(Path(__file__).with_name('workspace_files.py').resolve()), str(root),
                ]}
                if evidence is not None:
                    server['args'].append(str(evidence['server_path']))
                profile.write_text(
                    '---\nname: workbench-files\ndescription: Workbench workspace file access\n'
                    f'mcpServers: {json.dumps([{"workbench_files": server}])}\n---\n'
                    'Use the workbench_files tools when creating, editing, or deleting files.\n',
                    encoding='utf-8',
                )
                file_options = {'file_profile': profile, 'allow_git': grok_test_git_allowed(root)}
            prompt_path.write_bytes(agent_prompt.encode("utf-8"))
            command = build_grok_command(grok_command, prompt_path, working_directory,
                                         session_id, final_delivery, **file_options)
            if evidence is not None:
                command.extend(['--debug', '--debug-file', str(evidence['debug_path'])])
                evidence['command'] = command
            result = run_managed_process(
                assistant_message_id,
                command,
                cwd=working_directory,
                env=environment,
                diagnostic_metadata={
                    "agent_id": "grok",
                    "transport": "prompt-file",
                    "session_mode": "resume" if session_id else "new",
                },
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=AGENT_GROK_TIMEOUT_SECONDS,
            )
        answer, new_session_id, error_messages, permission_notes = parse_grok_output(result.stdout)
        if session_id and new_session_id != session_id:
            error_messages.append('Grok resumed session ID mismatch')
        if result.returncode != 0 or error_messages:
            if diagnostic_enabled:
                raise RuntimeError("Grok CLI 未通过完成校验，请查看该次运行的脱敏诊断摘要。")
            diagnostic = grok_diagnostic((result.stderr or "").strip())
            diagnostic = next(iter(error_messages), "") or diagnostic or "Grok CLI 执行失败"
            if permission_notes:
                diagnostic += '\n' + '\n'.join(permission_notes)
            raise RuntimeError(diagnostic[:4000])
        if not answer:
            raise RuntimeError("Grok 已退出，但没有返回可显示的最终回复。")
        if is_run_cancelled(assistant_message_id):
            raise AgentRunCancelled()
        if not session_id and new_session_id:
            run = active_run(assistant_message_id)
            with run['state_lock']:
                if run['cancel_requested']:
                    raise AgentRunCancelled()
                if run['execution'].session_claim is None:
                    save_agent_session_id(conversation_id, GROK_SESSION_KEY, new_session_id)
        if permission_notes and not diagnostic_enabled:
            answer = ("[工作台提示] CLI 诊断（已截断并脱敏）：\n"
                      + "\n".join(permission_notes) + '\n\n' + answer)
        return worker_reply(assistant_message_id, answer, "completed")
    except AgentRunCancelled:
        return worker_reply(assistant_message_id, "已停止。", "cancelled")
    except subprocess.TimeoutExpired:
        if is_run_cancelled(assistant_message_id):
            return worker_reply(assistant_message_id, "已停止。", "cancelled")
        else:
            return worker_reply(assistant_message_id, f"Grok 执行超时（{AGENT_GROK_TIMEOUT_SECONDS} 秒）。可能已有文件修改，未自动回退。", "failed")
    except Exception as error:
        if is_run_cancelled(assistant_message_id):
            return worker_reply(assistant_message_id, "已停止。", "cancelled")
        else:
            message = format_execution_error(error)
            return worker_reply(assistant_message_id, "调用 Grok 失败：" + message[:4000], "failed")
