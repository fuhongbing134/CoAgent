#!/usr/bin/env python3
"""P0-2 步骤 0：后端多 session 并发能力验证（第一层）。

直接启动两个独立 CLI 进程（绕过工作台的全局锁），各自在独立临时工作区
运行同一个「耗时可控」探针，测量两次探针的实际执行区间是否重叠。

用途：在投入 P0-2 实施之前，先确认某个后端（Codex / DeepSeek Harness）本身
能否同时跑两个不同 session。本脚本不修改项目文件、不操作业务数据库。

用法示例：
    python test_concurrency_backend.py --backend codex --sleep 20 --repeat 3
    python test_concurrency_backend.py --backend dsh   --sleep 20 --repeat 1

verdict 含义：
    pass      探针实际重叠 >= 阈值，且两侧成功、标记正确、session 不同、最终回复匹配。
    fail      运行失败、session 缺失/相同、最终回复缺失、或已观察到并发但某侧未成功。
    uncertain 重叠不足阈值（可能是串行，也可能是启动窗口不足），需重测。
    invalid   探针未真正运行或结果缺失/标记错误/字段非法。

每次运行建立唯一证据目录（--evidence-root/<backend>_<run_id>/），每轮先归档
stdout/stderr（解码后文本）与探针/最终回复（原始字节），再解析分类；若进程树
清理未确认，保留工作目录并打印路径。

说明：
- 仅支持 Windows（进程树清理依赖 taskkill；其它平台启动即拒绝）。
- 只验证「新建 + 新建」；恢复路径需先建 session，留作扩展。
- 重叠阈值 OVERLAP_THRESHOLD 是本轮工程约定，不是性能承诺。
- 重叠用 time.monotonic_ns()（Windows 基于 QueryPerformanceCounter，跨进程一致），
  UTC 时间仅用于展示。
- Windows 探针命令明确按 PowerShell 规则引用。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

OVERLAP_THRESHOLD = 2.0          # 秒；本轮工程约定，非性能承诺
OVERLAP_THRESHOLD_NS = int(OVERLAP_THRESHOLD * 1_000_000_000)
DEFAULT_SLEEP = 20
CLI_TIMEOUT = 600                # 每次 CLI 调用的上限（秒）
COMM_TIMEOUT = 30                # 二次输出收集 / 进程树清理的上限（秒）
MARKER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

# 探针：marker + sleep 秒数 + 输出绝对路径。重叠用 monotonic_ns（跨进程可比），UTC 仅展示。
# sleep 前/后分别立即输出并刷新事件，便于确认真实启动与取消位置。
PROBE_SOURCE = r'''import json, os, sys, time
from datetime import datetime, timezone

marker = sys.argv[1]
sleep_s = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
out = sys.argv[3] if len(sys.argv) > 3 else "probe_result.json"

def utc_now():
    return datetime.now(timezone.utc).isoformat()

start_mono_ns = time.monotonic_ns()
start_utc = utc_now()
print("PROBE_START", marker, start_mono_ns, start_utc, flush=True)
time.sleep(sleep_s)
end_mono_ns = time.monotonic_ns()
end_utc = utc_now()
print("PROBE_END", marker, end_mono_ns, end_utc, flush=True)
with open(out, "w", encoding="utf-8") as f:
    json.dump({
        "marker": marker,
        "start_mono_ns": start_mono_ns,
        "end_mono_ns": end_mono_ns,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "pid": os.getpid(),
    }, f)
'''


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    if candidates:
        return str(sorted(candidates)[0])
    return None


def resolve_dsh_command() -> str | None:
    configured = os.environ.get("DSH_CLI", "dsh").strip().strip('"')
    if not configured:
        return None
    located = shutil.which(configured)
    if located:
        return located
    candidate = Path(configured).expanduser()
    return str(candidate) if candidate.is_file() else None


def resolve_dsh_desktop_runtime(command: str) -> tuple[str, str] | None:
    """若 DSH 命令是桌面端 shim（.cmd/.bat），返回其 Electron 打包的 CLI 入口。"""
    if os.name != "nt" or Path(command).suffix.lower() not in {".cmd", ".bat"}:
        return None
    command_path = Path(command)
    try:
        resources_dir = command_path.parents[3]
    except IndexError:
        return None
    electron_exe = resources_dir.parent / "DeepSeek Harness.exe"
    app_asar = resources_dir / "app.asar"
    if not electron_exe.is_file() or not app_asar.is_file():
        return None
    cli_entry = str(
        resources_dir / "app.asar" / "dsh" / "node_modules"
        / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    )
    return str(electron_exe), cli_entry


def build_command(backend: str, cli: str, workdir: Path, answer: Path) -> tuple[list[str], bool]:
    """构造一个「全新 session」的 CLI 调用（prompt 走 stdin 的 `-`）。返回 (命令, 是否需要 ELECTRON_RUN_AS_NODE)。"""
    if backend == "codex":
        return [
            cli, "exec",
            "--sandbox", "workspace-write",
            "--cd", str(workdir),
            "--json", "--skip-git-repo-check",
            "--output-last-message", str(answer),
            "-",
        ], False
    if backend == "dsh":
        project_dir = Path(__file__).resolve().parents[2]
        patch = Path(os.environ.get("DSH_HEADLESS_PATCH", project_dir / "dsh-headless-auth.patch.yml")).expanduser()
        args = ["--profile", "headless", "--patch", str(patch), "--json", "-"]
        desktop_runtime = resolve_dsh_desktop_runtime(cli)
        if desktop_runtime:
            electron_exe, cli_entry = desktop_runtime
            return [electron_exe, "--expose-internals", cli_entry, *args], True
        if os.name == "nt" and Path(cli).suffix.lower() in {".cmd", ".bat"}:
            return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", cli, *args], False
        return [cli, *args], False
    raise SystemExit(f"不支持的 backend: {backend}")


def _ps_quote(s: str) -> str:
    """PowerShell 单引号引用：把内部单引号翻倍。"""
    return "'" + s.replace("'", "''") + "'"


def prompt_for(probe_abs: Path, marker: str, sleep_s: float, out_abs: Path) -> str:
    # 仅支持 Windows（main 已拒绝其它平台），因此走 PowerShell 分支。
    parts = [sys.executable, str(probe_abs), marker, str(sleep_s), str(out_abs)]
    cmd = "& " + " ".join(_ps_quote(p) for p in parts)
    return (
        f"在 PowerShell 中运行下面这条命令（用绝对路径），然后原样报告该命令的 stdout：\n"
        f"{cmd}\n"
        f"不要做任何其他事情，不要修改 probe.py。"
    )


def prepare_run(backend: str, cli: str, marker: str, sleep_s: float) -> dict:
    """创建临时工作区、写探针、构造命令与 prompt。返回可交给线程的 dict。"""
    if backend == "dsh":
        # DSH 沙箱（workspace-write）的 workspace 是项目目录；tempfile.mkdtemp() 的临时目录
        # 带受限 ACL 且不在 workspace 内，DSH 工具读不到 probe.py。改用项目目录下的唯一子目录，
        # 继承项目目录的正常 ACL，DSH 工具可访问。
        workdir = Path(__file__).resolve().parents[2] / f".conc-dsh-{uuid.uuid4().hex[:8]}"
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        workdir = Path(tempfile.mkdtemp(prefix=f"conc-{backend}-"))
    probe_abs = workdir / "probe.py"
    out_abs = workdir / "probe_result.json"
    answer = workdir / "answer.txt"
    (workdir / "probe.py").write_text(PROBE_SOURCE, encoding="utf-8")
    cmd, use_electron_as_node = build_command(backend, cli, workdir, answer)
    env = None
    if use_electron_as_node:
        env = os.environ.copy()
        env["ELECTRON_RUN_AS_NODE"] = "1"
    return {
        "workdir": workdir,
        "out_abs": out_abs,
        "answer": answer,
        "cmd": cmd,
        "env": env,
        "prompt": prompt_for(probe_abs, marker, sleep_s, out_abs),
        "marker": marker,
    }


def kill_tree(proc: subprocess.Popen) -> bool:
    """终止子进程及其子进程树；返回是否成功（无法确认返回 False）。仅 Windows。"""
    if os.name != "nt":
        return False  # main 已拒绝其它平台，防御性兜底
    ok = True
    if proc.pid is not None:
        try:
            r = subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=COMM_TIMEOUT,
            )
            if r.returncode != 0:
                ok = False
        except Exception:
            ok = False
    try:
        proc.kill()
    except Exception:
        pass
    return ok


def _to_str(data) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def _cleanup_and_collect(proc: subprocess.Popen, result: dict, partial_out: str, partial_err: str) -> None:
    """异常/超时后的统一清理：杀进程树 + 有界二次收集；分别保留 partial/final 快照。"""
    result["kill_tree_ok"] = kill_tree(proc)
    try:
        out2, err2 = proc.communicate(timeout=COMM_TIMEOUT)
        result["second_communicate_ok"] = True
    except Exception:
        out2, err2 = "", ""
        result["second_communicate_ok"] = False
    result["out_partial"] = partial_out or ""
    result["err_partial"] = partial_err or ""
    result["out_final"] = _to_str(out2)
    result["err_final"] = _to_str(err2)
    result["cleanup_failed"] = (
        (not result["kill_tree_ok"])
        or (not result["second_communicate_ok"])
        or (proc.poll() is None)
    )


def _run_one(prep: dict, result: dict) -> None:
    """Popen + communicate（读取全部输出避免管道堵塞），结果写入 result。"""
    result["start_error"] = False
    result["cleanup_failed"] = False
    result["timeout"] = False
    result["started_at_mono_ns"] = time.monotonic_ns()
    result["started_at_utc"] = _utc_now()

    try:
        proc = subprocess.Popen(
            prep["cmd"],
            cwd=str(prep["workdir"]),
            env=prep.get("env"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as error:
        result["rc"] = -3
        result["out"] = ""
        result["err"] = f"启动失败：{type(error).__name__}: {error}"
        result["start_error"] = True
        result["exited_at_mono_ns"] = time.monotonic_ns()
        result["exited_at_utc"] = _utc_now()
        return

    result["pid"] = proc.pid
    result["popen_at_mono_ns"] = time.monotonic_ns()
    result["popen_at_utc"] = _utc_now()

    try:
        out, err = proc.communicate(input=prep["prompt"], timeout=CLI_TIMEOUT)
        result["rc"] = proc.returncode
        result["out"] = out or ""
        result["err"] = err or ""
    except subprocess.TimeoutExpired as error:
        result["timeout"] = True
        _cleanup_and_collect(proc, result, _to_str(error.output), _to_str(error.stderr))
        result["rc"] = -2
        result["out"] = result["out_partial"]
        result["err"] = result["err_partial"]
    except Exception as error:
        result["communicate_error"] = f"{type(error).__name__}: {error}"
        _cleanup_and_collect(proc, result, "", "")
        result["rc"] = -4
        result["out"] = result["out_final"]
        result["err"] = result["err_final"]

    result["exited_at_mono_ns"] = time.monotonic_ns()
    result["exited_at_utc"] = _utc_now()


def read_probe_bytes(workdir: Path) -> bytes | None:
    """字节级读取探针结果，避免解码失败丢证据；返回 bytes 或 None。"""
    path = workdir / "probe_result.json"
    if not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def parse_probe(raw: bytes | None) -> dict | None:
    """从原始字节解析探针结果；任何解码/JSON 异常都返回 None，不抛。"""
    if raw is None:
        return None
    try:
        text = raw.decode("utf-8", errors="replace")
        data = json.loads(text)
    except Exception:  # UnicodeDecodeError / JSONDecodeError / ValueError / RecursionError 等
        return None
    return data if isinstance(data, dict) else None


def parse_probe_from_reply(text: str, marker: str) -> dict | None:
    """从最终回复（探针 stdout 原样报告）解析 PROBE_START/END 的 monotonic_ns 与 UTC。

    优先于文件：codex 的 workspace-write 沙箱可能把探针写盘文件隔离在沙箱内，
    主机侧读不到；而 stdout 被 agent 原样报告进最终回复，是可靠可见的。
    """
    start_ns = end_ns = None
    start_utc = end_utc = None
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        if parts[0] == "PROBE_START" and parts[1] == marker:
            try:
                start_ns = int(parts[2])
                start_utc = parts[3] if len(parts) > 3 else None
            except ValueError:
                pass
        elif parts[0] == "PROBE_END" and parts[1] == marker:
            try:
                end_ns = int(parts[2])
                end_utc = parts[3] if len(parts) > 3 else None
            except ValueError:
                pass
    if start_ns is None or end_ns is None:
        return None
    return {"marker": marker, "start_mono_ns": start_ns, "end_mono_ns": end_ns,
            "start_utc": start_utc, "end_utc": end_utc}


def parse_session_id(backend: str, stdout: str) -> str | None:
    for line in (stdout or "").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        if backend == "codex" and ev.get("type") == "thread.started" and isinstance(ev.get("thread_id"), str):
            return ev["thread_id"]
        if backend == "dsh" and ev.get("type") == "session" and isinstance(ev.get("sessionId"), str):
            return ev["sessionId"]
    return None


def extract_final_reply(backend: str, stdout: str, answer_path: Path) -> tuple[str, bool]:
    """返回 (最终回复文本, 是否成功提取)。绝不回退整份 stdout。"""
    if backend == "codex":
        try:
            if answer_path.is_file():
                return answer_path.read_text(encoding="utf-8", errors="replace").strip(), True
        except OSError:
            pass
        return "", False
    if backend == "dsh":
        for line in (stdout or "").splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict) and ev.get("type") == "final":
                text = ev.get("text")
                if isinstance(text, str):
                    return text.strip(), True
        return "", False
    return "", False


def _valid_ns(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _safe_get(data: dict | None, key: str):
    if isinstance(data, dict):
        return data.get(key)
    return None


def validate_probe(data: dict | None, marker: str, sleep_s: float) -> str | None:
    if data is None:
        return "探针结果缺失或不是 JSON 对象"
    if data.get("marker") != marker:
        return f"探针标记错误（{data.get('marker')!r} 期望 {marker!r}）"
    start_ns = data.get("start_mono_ns")
    end_ns = data.get("end_mono_ns")
    if not _valid_ns(start_ns) or not _valid_ns(end_ns):
        return "探针时间字段非法（非整数/非有限数值）"
    if end_ns < start_ns:
        return "探针结束早于开始"
    duration = (end_ns - start_ns) / 1_000_000_000
    if duration < sleep_s - 1.0:
        return f"探针实际时长 {duration:.2f}s 明显短于设定 {sleep_s}s（未真正等待）"
    return None


def classify(backend: str, prep_a: dict, prep_b: dict,
             res_a: dict, res_b: dict, ra: dict | None, rb: dict | None,
             sleep_s: float) -> tuple[str, str]:
    rc_a, rc_b = res_a.get("rc", -9), res_b.get("rc", -9)
    if rc_a != 0 or rc_b != 0:
        return "fail", f"运行失败（A 退出码 {rc_a}，B 退出码 {rc_b}）"

    err_a = validate_probe(ra, prep_a["marker"], sleep_s)
    err_b = validate_probe(rb, prep_b["marker"], sleep_s)
    if err_a or err_b:
        return "invalid", f"探针记录无效：A={err_a or 'ok'}；B={err_b or 'ok'}"

    sid_a = parse_session_id(backend, res_a.get("out", ""))
    sid_b = parse_session_id(backend, res_b.get("out", ""))
    if not sid_a or not sid_b:
        return "fail", f"未能解析 session ID（A={sid_a!r}，B={sid_b!r}）"
    if sid_a == sid_b:
        return "fail", "两个调用解析出相同 session ID"

    reply_a, found_a = extract_final_reply(backend, res_a.get("out", ""), prep_a["answer"])
    reply_b, found_b = extract_final_reply(backend, res_b.get("out", ""), prep_b["answer"])
    if not found_a or not found_b:
        return "fail", "无法提取最终回复（不回退整份 stdout）"
    if prep_a["marker"] not in reply_a or prep_b["marker"] not in reply_b:
        return "fail", "最终回复未包含各自标记"

    overlap_ns = min(ra["end_mono_ns"], rb["end_mono_ns"]) - max(ra["start_mono_ns"], rb["start_mono_ns"])
    overlap = overlap_ns / 1_000_000_000
    if overlap < OVERLAP_THRESHOLD:
        return "uncertain", f"重叠不足（{overlap:.1f}s < {OVERLAP_THRESHOLD}s）：可能是串行或窗口不足，建议延长 sleep 重测"
    return "pass", f"实际重叠 {overlap:.1f}s，两侧成功、标记正确、session 不同"


def get_cli_version(cli: str) -> str:
    try:
        r = subprocess.run(
            [cli, "--version"], capture_output=True, text=True,
            timeout=15, encoding="utf-8", errors="replace",
        )
        if r.returncode == 0:
            return (r.stdout or r.stderr).strip().splitlines()[0][:200]
    except Exception:
        pass
    return "unknown"


def _archive_raw(prefix: Path, prep: dict, res: dict, probe_bytes: bytes | None) -> None:
    """归档原始输出/探针/最终回复；stdout/stderr 为解码后文本，probe/answer 为原始字节。写入失败仅告警，不阻断。"""
    try:
        prefix.mkdir(parents=True, exist_ok=True)
        (prefix / "stdout.txt").write_text(res.get("out", ""), encoding="utf-8", errors="replace")
        (prefix / "stderr.txt").write_text(res.get("err", ""), encoding="utf-8", errors="replace")
        for key, name in (("out_partial", "stdout_partial.txt"),
                          ("err_partial", "stderr_partial.txt"),
                          ("out_final", "stdout_final.txt"),
                          ("err_final", "stderr_final.txt")):
            if res.get(key) is not None:
                (prefix / name).write_text(res.get(key, ""), encoding="utf-8", errors="replace")
        answer = prep["answer"]
        try:
            if answer.is_file():
                (prefix / "answer.txt").write_bytes(answer.read_bytes())
        except OSError:
            pass
        if probe_bytes is not None:
            (prefix / "probe_raw.bin").write_bytes(probe_bytes)
            (prefix / "probe_raw.txt").write_text(probe_bytes.decode("utf-8", errors="replace"), encoding="utf-8")
    except OSError as error:
        print(f"  [归档告警] {prefix}: {type(error).__name__}: {error}")


def _archive_meta(prefix: Path, prep: dict, res: dict, session_id: str | None,
                  final_reply: str, final_found: bool) -> None:
    """归档解析后的元数据（session、最终回复、调用时间、清理结果）。"""
    if final_found:
        (prefix / "final_reply.txt").write_text(final_reply, encoding="utf-8", errors="replace")
    meta = {
        "backend_marker": prep["marker"],
        "rc": res.get("rc"),
        "pid": res.get("pid"),
        "timeout": res.get("timeout", False),
        "start_error": res.get("start_error", False),
        "cleanup_failed": res.get("cleanup_failed", False),
        "kill_tree_ok": res.get("kill_tree_ok"),
        "second_communicate_ok": res.get("second_communicate_ok"),
        "communicate_error": res.get("communicate_error"),
        "session_id": session_id,
        "final_reply_found": final_found,
        "workdir": str(prep["workdir"]),
        "started_at_mono_ns": res.get("started_at_mono_ns"),
        "started_at_utc": res.get("started_at_utc"),
        "popen_at_mono_ns": res.get("popen_at_mono_ns"),
        "popen_at_utc": res.get("popen_at_utc"),
        "exited_at_mono_ns": res.get("exited_at_mono_ns"),
        "exited_at_utc": res.get("exited_at_utc"),
    }
    (prefix / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    if os.name != "nt":
        print("[失败] 本脚本仅支持 Windows（进程树清理依赖 taskkill）。")
        return 2

    ap = argparse.ArgumentParser(description="后端多 session 并发能力验证")
    ap.add_argument("--backend", required=True, choices=["codex", "dsh"])
    ap.add_argument("--sleep", type=float, default=DEFAULT_SLEEP)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--marker-a", default="GROUP_A")
    ap.add_argument("--marker-b", default="GROUP_B")
    ap.add_argument("--evidence-root", default=str(Path(__file__).resolve().parents[2] / "test_evidence"))
    args = ap.parse_args()

    if args.repeat <= 0:
        print("[失败] --repeat 必须为正整数。")
        return 2
    if not (isinstance(args.sleep, float) and math.isfinite(args.sleep) and args.sleep > 0):
        print("[失败] --sleep 必须为有限正数。")
        return 2
    for m in (args.marker_a, args.marker_b):
        if not MARKER_RE.match(m):
            print(f"[失败] marker 必须是简单标识符（字母数字 _ . -，最长 64）：{m!r}")
            return 2
    if args.sleep < OVERLAP_THRESHOLD:
        print(f"[提示] --sleep={args.sleep}s 小于重叠阈值 {OVERLAP_THRESHOLD}s，可能无法完成预期验收。")

    cli = resolve_codex_command() if args.backend == "codex" else resolve_dsh_command()
    if not cli:
        print(f"[失败] 找不到 {args.backend} CLI，请安装或设置对应环境变量。")
        return 2

    run_id = uuid.uuid4().hex[:12]
    run_dir = Path(args.evidence_root) / f"{args.backend}_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[信息] backend={args.backend} cli={cli} sleep={args.sleep}s repeat={args.repeat}")
    print(f"[信息] 重叠阈值={OVERLAP_THRESHOLD}s（工程约定） 解释器={sys.executable}")
    print(f"[信息] 证据目录={run_dir}")

    run_meta = {
        "backend": args.backend,
        "cli": cli,
        "cli_version": get_cli_version(cli),
        "python_version": sys.version,
        "python_executable": sys.executable,
        "sleep": args.sleep,
        "repeat": args.repeat,
        "overlap_threshold": OVERLAP_THRESHOLD,
        "run_id": run_id,
        "started_utc": _utc_now(),
    }
    (run_dir / "run_meta.json").write_text(json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    outcomes = []
    for i in range(1, args.repeat + 1):
        t0 = time.time()
        batch = f"{run_id}_r{i}"
        marker_a = f"{args.marker_a}_{batch}"
        marker_b = f"{args.marker_b}_{batch}"
        prep_a = prepare_run(args.backend, cli, marker_a, args.sleep)
        prep_b = prepare_run(args.backend, cli, marker_b, args.sleep)
        res_a, res_b = {}, {}
        ta = threading.Thread(target=_run_one, args=(prep_a, res_a))
        tb = threading.Thread(target=_run_one, args=(prep_b, res_b))
        ta.start(); tb.start()
        ta.join(); tb.join()

        # G1：先按字节归档原始输出/探针，再做任何可能抛异常的解析。
        round_dir = run_dir / f"round{i:02d}"
        probe_bytes_a = read_probe_bytes(prep_a["workdir"])
        probe_bytes_b = read_probe_bytes(prep_b["workdir"])
        _archive_raw(round_dir / "A", prep_a, res_a, probe_bytes_a)
        _archive_raw(round_dir / "B", prep_b, res_b, probe_bytes_b)

        # 解析（全部 try/except，异常不阻断归档，也不阻断后续轮次）。
        ra = rb = None
        sid_a = sid_b = None
        reply_a = reply_b = ""
        found_a = found_b = False
        parse_error = None
        try:
            reply_a, found_a = extract_final_reply(args.backend, res_a.get("out", ""), prep_a["answer"])
            reply_b, found_b = extract_final_reply(args.backend, res_b.get("out", ""), prep_b["answer"])
            # 优先从最终回复（探针 stdout 原样报告）解析时间戳；文件因沙箱可能不可见，只作兜底。
            ra = parse_probe_from_reply(reply_a, prep_a["marker"]) or parse_probe(probe_bytes_a)
            rb = parse_probe_from_reply(reply_b, prep_b["marker"]) or parse_probe(probe_bytes_b)
            sid_a = parse_session_id(args.backend, res_a.get("out", ""))
            sid_b = parse_session_id(args.backend, res_b.get("out", ""))
        except Exception as error:
            parse_error = f"{type(error).__name__}: {error}"

        if parse_error:
            verdict, detail = "invalid", f"解析异常：{parse_error}"
        else:
            try:
                verdict, detail = classify(args.backend, prep_a, prep_b, res_a, res_b, ra, rb, args.sleep)
            except Exception as error:
                verdict, detail = "invalid", f"分类异常：{type(error).__name__}: {error}"

        outcomes.append(verdict)
        start_a = _safe_get(ra, "start_utc")
        start_b = _safe_get(rb, "start_utc")
        print(
            f"[第{i}次] verdict={verdict} rcA={res_a.get('rc')} rcB={res_b.get('rc')} "
            f"startA={start_a} startB={start_b} wall={time.time() - t0:.1f}s | {detail}"
        )

        _archive_meta(round_dir / "A", prep_a, res_a, sid_a, reply_a, found_a)
        _archive_meta(round_dir / "B", prep_b, res_b, sid_b, reply_b, found_b)
        (round_dir / "verdict.txt").write_text(f"{verdict}\n{detail}\n", encoding="utf-8")

        for prep, res in ((prep_a, res_a), (prep_b, res_b)):
            if res.get("cleanup_failed") or res.get("start_error"):
                print(f"  [保留工作目录] {prep['workdir']}（清理未确认/启动异常）")
            else:
                shutil.rmtree(prep["workdir"], ignore_errors=True)

    passed = outcomes.count("pass")
    print(f"[汇总] pass={passed}/{len(outcomes)} verdicts={outcomes}")
    print(f"[证据] 已归档到 {run_dir}")
    if passed == len(outcomes):
        print(f"[结论] {args.backend}：本轮观察到实际执行重叠且两侧成功。")
        return 0
    print(f"[结论] {args.backend}：未全部通过，保留证据与失败分类。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
