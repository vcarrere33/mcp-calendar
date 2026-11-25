"""Spike: can a PydanticAI agent complete the MCP OAuth flow (Google sign-in) on its own?

Step 5 of the plan:  uv run python spikes/whoami_agent.py
Step 6 (cached):     uv run python spikes/whoami_agent.py --cache   (run twice: 2nd run must not open a browser)
"""

import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from fastmcp.client.auth import OAuth
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

load_dotenv()
MCP_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8000/mcp")
TOKEN_DIR = Path(".tokens/agent-oauth")
# The browser sign-in runs inside the MCP connection handshake, and pydantic-ai's default
# init_timeout is 5 s: too short for a human to sign in with Google. Match FastMCP's
# own 300 s callback timeout.
SIGN_IN_TIMEOUT = 300.0


def build_toolset(cache: bool) -> MCPToolset:
    if not cache:
        # Built-in shortcut: FastMCP's OAuth flow, tokens kept in memory only.
        return MCPToolset(MCP_URL, auth="oauth", init_timeout=SIGN_IN_TIMEOUT)
    # Keys contain the server URL ("http://localhost:8000/mcp"); without the sanitization
    # strategies its slashes become nested folders and the write fails. The strategies
    # measure path limits on the folder, so it must exist first.
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    store = FileTreeStore(
        data_directory=TOKEN_DIR,
        key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(TOKEN_DIR),
        collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(TOKEN_DIR),
    )
    oauth = OAuth(mcp_url=MCP_URL, client_name="booking-agent spike", token_storage=store)
    return MCPToolset(MCP_URL, auth=oauth, init_timeout=SIGN_IN_TIMEOUT)


async def main(cache: bool, model: str) -> None:
    agent = Agent(model, toolsets=[build_toolset(cache)])
    async with agent:
        result = await agent.run("Call the whoami tool and tell me my email address.")
    print(result.output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cache", action="store_true", help="cache OAuth tokens in .tokens/agent-oauth"
    )
    parser.add_argument("--model", default=os.environ.get("AGENT_MODEL", "ollama:qwen2.5:7b"))
    args = parser.parse_args()
    asyncio.run(main(args.cache, args.model))
