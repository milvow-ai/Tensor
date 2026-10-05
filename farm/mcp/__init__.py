"""Any MCP server through the Farm, as is (OPEN1).

* ``naming``   the names exposed tools carry (``<namespace>__<tool>``)
* ``store``    the pass-through providers and their cached tool catalogues in Postgres
* ``sync``     ``farm mcp sync``: list a server's tools through one of its accounts into the catalogue
* ``expose``   which tools are published directly and which only through discovery
* ``importer`` ``farm mcp import``: take server definitions from Claude Desktop / Claude Code / Codex

Nothing is imported here on purpose: the registry models and the executors import this package's leaf modules.
"""
