"""Local MCP message bridge entry point."""

import os
from pathlib import Path

from workbench.mcp import bridge
from workbench.mcp.bridge import (
    database, initialize_db, list_agents, mcp, read_conversation, read_inbox,
    register_agent, send_message,
)

PROJECT_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("AGENT_BRIDGE_DB", PROJECT_DIR / "agent_bridge.db")).expanduser().resolve()
AGENT_ID = os.environ.get("AGENT_BRIDGE_AGENT_ID", "unknown-agent")
bridge.DB_PATH = DB_PATH
bridge.AGENT_ID = AGENT_ID


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-legacy-upgrade', action='store_true', help='Confirm all legacy writers are stopped')
    args = parser.parse_args()
    initialize_db(allow_legacy=args.allow_legacy_upgrade)
    mcp.run(transport="stdio")
