"""Spike: the smallest FastMCP server that signs users in with Google.

It exposes one tool, `whoami`, that returns the email Google verified for the caller.
Run:  uv run python spikes/whoami_server.py   (port: MCP_PORT, default 8000)
"""

import os

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.dependencies import get_access_token


def build_server(port: int) -> FastMCP:
    auth = GoogleProvider(
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        base_url=f"http://localhost:{port}",
        required_scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"],
    )
    mcp = FastMCP("whoami", auth=auth)

    @mcp.tool
    def whoami() -> str:
        """Return the email address of the signed-in user, as verified by Google."""
        token = get_access_token()
        if token is None:
            return "not signed in"
        return token.claims.get("email") or "signed in, but Google returned no email"

    return mcp


if __name__ == "__main__":
    load_dotenv()
    port = int(os.environ.get("MCP_PORT", "8000"))
    build_server(port).run(transport="http", host="localhost", port=port)
