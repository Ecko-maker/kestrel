"""A tiny stdio MCP server for tests: `ok` works, `crash` kills the whole process."""

import os

from mcp.server.mcpserver import MCPServer

server = MCPServer("crashy")


@server.tool()
def ok() -> str:
    """Always works."""
    return "fine"


@server.tool()
def crash() -> str:
    """Kills the server process mid-call."""
    os._exit(1)


if __name__ == "__main__":
    server.run("stdio")
