"""Kestrel's standalone MCP servers (Phase 5): read-only views of its eval results and traces.

They live outside src/kestrel/ on purpose: they aren't part of the agent, so changing them never changes
the agent or triggers the evals. Run them from the repo with `uv run --directory <repo> python -m servers.<name>`.
"""
