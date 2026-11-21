import os
from pathlib import Path

def resolve_dsh_desktop_runtime(command: str) -> tuple[str, str] | None:
    """Find the Electron-bundled DSH CLI behind the DeepSeek Harness desktop shim."""
    if os.name != "nt" or Path(command).suffix.lower() not in {".cmd", ".bat"}:
        return None

    command_path = Path(command)
    try:
        # The desktop shim lives at resources/runtime/cli/bin/dsh.cmd.
        resources_dir = command_path.parents[3]
    except IndexError:
        return None

    electron_exe = resources_dir.parent / "DeepSeek Harness.exe"
    app_asar = resources_dir / "app.asar"
    if not electron_exe.is_file() or not app_asar.is_file():
        return None

    # Electron resolves this path from app.asar. It is intentionally not checked
    # with Path.is_file(), because the entry is inside the ASAR archive.
    cli_entry = str(
        resources_dir
        / "app.asar"
        / "dsh"
        / "node_modules"
        / "@deepseek-ai"
        / "dsh"
        / "lib"
        / "bin.js"
    )
    return str(electron_exe), cli_entry


def build_dsh_command(command: str, arguments: list[str]) -> tuple[list[str], bool, bool]:
    """Build a DSH command, bypassing the desktop-host shim when available."""
    desktop_runtime = resolve_dsh_desktop_runtime(command)
    if desktop_runtime:
        electron_exe, cli_entry = desktop_runtime
        return [electron_exe, "--expose-internals", cli_entry, *arguments], False, True
    if os.name == "nt" and Path(command).suffix.lower() in {".cmd", ".bat"}:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", command, *arguments], False, False
    return [command, *arguments], False, False
