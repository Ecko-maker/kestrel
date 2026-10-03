import json

import pytest

from kestrel import tools
from kestrel.tools import ToolRegistry, build_schema


def test_schema_from_hints_and_docstring():
    def search(query: str, max_results: int = 5, exact: bool = False) -> list:
        """Search for things.

        Args:
            query: What to look for.
            max_results: How many to return.
        """

    fn = build_schema(search)["function"]
    assert fn["name"] == "search"
    assert fn["description"] == "Search for things."
    params = fn["parameters"]
    assert params["required"] == ["query"]
    assert params["properties"]["query"] == {"type": "string", "description": "What to look for."}
    assert params["properties"]["max_results"] == {
        "type": "integer",
        "description": "How many to return.",
        "default": 5,
    }
    assert params["properties"]["exact"] == {"type": "boolean", "default": False}


def test_starter_tools_registered():
    risks = {name: t.risk for name, t in tools.registry.tools.items()}
    assert risks == {
        "get_current_time": "safe",
        "calculator": "safe",
        "list_files": "safe",
        "read_file": "safe",
        "web_search": "safe",
        "write_file": "confirm",
        "append_to_file": "confirm",
        "create_note": "confirm",
        "send_message": "confirm",
        "delete_file": "forbidden",
    }


@pytest.mark.parametrize(
    "expr, expected",
    [
        ("0.175 * 2340", "409.5"),
        ("2,340 * 0.175", "409.5"),
        ("(1 + 2) ** 3", "27"),
        ("-7 // 2", "-4"),
        ("sqrt(16) + abs(-1)", "5.0"),
        ("round(pi, 2)", "3.14"),
    ],
)
def test_calculator(expr, expected):
    assert tools.calculator(expr) == expected


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('dir')",
        "open('x')",
        "(1).__class__",
        "[x for x in ()]",
        "lambda: 1",
        "9 ** 99999",
        "'a' * 3",
    ],
)
def test_calculator_rejects_code(expr):
    result = tools.registry.execute("calculator", {"expression": expr})
    assert result.startswith("Error:")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "notes.txt").write_text("hello notes", encoding="utf-8")
    (ws / "sub").mkdir()
    (ws / ".env").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / "secrets.txt").write_text("outside", encoding="utf-8")
    monkeypatch.setattr(tools, "WORKSPACE", ws)
    return ws


def test_read_and_list_inside_workspace(workspace):
    assert tools.read_file("notes.txt") == "hello notes"
    assert "notes.txt" in tools.list_files()
    assert "sub/" in tools.list_files(".")


@pytest.mark.parametrize(
    "path",
    [
        "../secrets.txt",
        "..\\secrets.txt",
        "sub/../../secrets.txt",
        "..\\..\\secrets",
        "C:\\Windows\\win.ini",
        "/etc/passwd",
        "\\secrets.txt",
        ".env",
        "sub/../.env",
        ".env.local",
    ],
)
def test_read_file_blocks_escapes_and_env(workspace, path):
    result = tools.registry.execute("read_file", {"path": path})
    assert result.startswith("Error: PermissionError")


def test_list_files_blocks_escape(workspace):
    assert tools.registry.execute("list_files", {"path": ".."}).startswith("Error: PermissionError")


def test_execute_turns_errors_into_text():
    reg = ToolRegistry()

    @reg.register
    def boom() -> str:
        """Always fails."""
        raise RuntimeError("kaboom")

    assert reg.execute("boom", "{}") == "Error: RuntimeError: kaboom"
    assert reg.execute("nope", "{}").startswith("Error: unknown tool")
    assert reg.execute("boom", "not json").startswith("Error: arguments are not valid JSON")


def test_execute_serializes_non_string_results():
    reg = ToolRegistry()

    @reg.register
    def pair() -> list:
        """Two numbers."""
        return [1, 2]

    assert json.loads(reg.execute("pair", {})) == [1, 2]
