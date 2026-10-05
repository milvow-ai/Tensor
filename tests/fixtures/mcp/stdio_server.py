"""A real MCP server over stdio, started by the Farm as a subprocess in the tests.

Self-contained (it runs with the interpreter alone, no repo imports). ``env_probe`` reports what the child
process was given, which is how the tests prove that only the configured variables reach a third-party server.
"""

import os

from fastmcp import FastMCP

server = FastMCP("stdio-fake")


@server.tool
def echo(message: str) -> str:
    """Echo the message back."""
    return f"stdio echo: {message}"


@server.tool
def env_probe(name: str) -> str:
    """The value of an environment variable of this process, or <unset>."""
    return os.environ.get(name, "<unset>")


if __name__ == "__main__":
    server.run(transport="stdio", show_banner=False)
