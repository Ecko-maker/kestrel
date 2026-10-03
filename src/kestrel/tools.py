"""Tools the model can call, plus a registry that turns plain Python functions into them.

Decorate a function with @tool and its OpenAI-style JSON schema is built from the
type hints and docstring, so the code and the schema the model sees never drift apart.

The registry is also the safety boundary between the model and real code: it checks
arguments before running anything, enforces a time limit, caps result size, and turns
every failure into text the model can read and recover from.
"""

import ast
import inspect
import json
import math
import operator
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, get_origin, get_type_hints
from zoneinfo import ZoneInfo

# Python type -> JSON Schema type
JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}

TOOL_TIMEOUT = 20.0       # seconds a single tool call may run
MAX_RESULT_CHARS = 8_000  # longer results are cut so one tool can't flood the context window


def _base_type(hint: Any) -> Any:
    return get_origin(hint) or hint  # list[str] -> list


def _type_ok(value: Any, expected: type) -> bool:
    if isinstance(value, bool):  # bool is a subclass of int in Python, but not in JSON
        return expected is bool
    if expected is float:
        return isinstance(value, (int, float))
    return isinstance(value, expected)


@dataclass
class Tool:
    name: str
    func: Callable[..., Any]
    schema: dict

    def check_args(self, args: dict) -> str | None:
        """Describe what's wrong with the arguments, or return None if they're fine."""
        params = inspect.signature(self.func).parameters
        hints = get_type_hints(self.func)
        if unknown := [k for k in args if k not in params]:
            return f"unexpected argument(s) {unknown}; allowed: {list(params)}"
        if missing := [n for n, p in params.items() if p.default is inspect.Parameter.empty and n not in args]:
            return f"missing required argument(s) {missing}"
        for key, value in args.items():
            expected = _base_type(hints.get(key, Any))
            if expected is Any or _type_ok(value, expected):
                continue
            wanted = JSON_TYPES.get(expected, getattr(expected, "__name__", str(expected)))
            return f"'{key}' must be a {wanted}, got {type(value).__name__} {value!r}"
        return None


def build_schema(func: Callable[..., Any]) -> dict:
    """Build an OpenAI tool schema from a function's signature and Google-style docstring."""
    doc = inspect.getdoc(func) or ""
    summary, _, args_section = doc.partition("Args:")
    arg_docs = dict(re.findall(r"^\s*(\w+):\s*(.+)$", args_section, re.MULTILINE))

    hints = get_type_hints(func)
    properties, required = {}, []
    for name, param in inspect.signature(func).parameters.items():
        prop = {"type": JSON_TYPES.get(_base_type(hints.get(name, str)), "string")}
        if name in arg_docs:
            prop["description"] = arg_docs[name].strip()
        if param.default is inspect.Parameter.empty:
            required.append(name)
        else:
            prop["default"] = param.default
        properties[name] = prop

    return {
        "type": "function",
        "function": {
            "name": func.__name__,
            "description": " ".join(summary.split()),
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def parse_arguments(arguments: str | dict | None) -> dict:
    """Turn the model's arguments into a dict, or raise ValueError with a readable reason."""
    if isinstance(arguments, dict):
        return arguments
    if not arguments or not arguments.strip():
        return {}
    try:
        args = json.loads(arguments)
        if isinstance(args, str):  # some models double-encode the JSON as a string
            args = json.loads(args)
    except json.JSONDecodeError as e:
        raise ValueError(f"arguments are not valid JSON ({e.msg} at position {e.pos}): {arguments[:200]!r}") from None
    if not isinstance(args, dict):
        raise ValueError(f'arguments must be a JSON object like {{"name": value}}, got {type(args).__name__}')
    return args


def _run_with_timeout(func: Callable[..., Any], args: dict, timeout: float) -> Any:
    """Run func on a daemon thread. Python can't kill a thread, so a stuck tool is
    abandoned (it finishes in the background) rather than blocking the agent."""
    box: dict[str, Any] = {}

    def target():
        try:
            box["result"] = func(**args)
        except BaseException as e:
            box["error"] = e

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"tool did not finish within {timeout:g}s")
    if "error" in box:
        raise box["error"]
    return box["result"]


class ToolRegistry:
    def __init__(self, timeout: float = TOOL_TIMEOUT, max_result_chars: int = MAX_RESULT_CHARS):
        self.tools: dict[str, Tool] = {}
        self.timeout = timeout
        self.max_result_chars = max_result_chars

    def register(self, func: Callable[..., Any]) -> Callable[..., Any]:
        self.tools[func.__name__] = Tool(func.__name__, func, build_schema(func))
        return func

    def schemas(self) -> list[dict]:
        return [t.schema for t in self.tools.values()]

    def execute(self, name: str, arguments: str | dict | None) -> str:
        """Run a tool and return its result as text. Every failure comes back as text too,
        so the model can read it and correct itself instead of the app crashing."""
        if name not in self.tools:
            return f"Error: unknown tool '{name}'. Available tools: {', '.join(self.tools)}"
        tool = self.tools[name]
        try:
            args = parse_arguments(arguments)
        except ValueError as e:
            return f"Error: {e}"
        if problem := tool.check_args(args):
            expected = json.dumps(tool.schema["function"]["parameters"])
            return f"Error: invalid arguments for {name}: {problem}. Expected: {expected}"
        try:
            result = _run_with_timeout(tool.func, args, self.timeout)
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        if len(text) > self.max_result_chars:
            text = (text[: self.max_result_chars]
                    + f"\n...[truncated: showing {self.max_result_chars:,} of {len(text):,} characters]")
        return text


registry = ToolRegistry()
tool = registry.register  # the @tool decorator


# --- Starter tools -------------------------------------------------------------


@tool
def get_current_time(timezone: str) -> str:
    """Get the current date and time in a timezone.

    Args:
        timezone: IANA timezone name, e.g. "Asia/Tokyo" or "America/New_York".
    """
    now = datetime.now(ZoneInfo(timezone))
    return now.strftime("%A %Y-%m-%d %H:%M:%S %Z (UTC%z)")


_OPERATORS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_FUNCTIONS = {"sqrt": math.sqrt, "abs": abs, "round": round, "min": min, "max": max}
_CONSTANTS = {"pi": math.pi, "e": math.e}


def _eval_node(node: ast.AST) -> float:
    match node:
        case ast.Constant(value=v) if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
        case ast.Name(id=name) if name in _CONSTANTS:
            return _CONSTANTS[name]
        case ast.UnaryOp(op=op, operand=x) if type(op) in _OPERATORS:
            return _OPERATORS[type(op)](_eval_node(x))
        case ast.BinOp(left=a, op=op, right=b) if type(op) in _OPERATORS:
            left, right = _eval_node(a), _eval_node(b)
            if isinstance(op, ast.Pow) and abs(right) > 1000:
                raise ValueError("exponent too large")
            return _OPERATORS[type(op)](left, right)
        case ast.Call(func=ast.Name(id=name), args=args, keywords=[]) if name in _FUNCTIONS:
            return _FUNCTIONS[name](*(_eval_node(a) for a in args))
    raise ValueError(f"unsupported expression: {ast.dump(node)[:60]}")


@tool
def calculator(expression: str) -> str:
    """Evaluate a math expression exactly. Supports + - * / // % **, parentheses,
    sqrt, abs, round, min, max, pi and e. Note: % is the remainder operator, not percent;
    write "17.5% of 2340" as "0.175 * 2340".

    Args:
        expression: The expression, e.g. "0.175 * 2340" or "sqrt(2) ** 2".
    """
    expression = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", expression)  # "2,340" -> "2340"
    tree = ast.parse(expression, mode="eval")
    result = _eval_node(tree.body)
    return str(round(result, 10) if isinstance(result, float) else result)


WORKSPACE = Path("workspace").resolve()


def _safe_path(path: str) -> Path:
    """Resolve a path inside WORKSPACE, refusing anything that escapes it or is a .env file."""
    p = Path(path)
    if p.anchor:  # absolute ("C:\...", "/etc") or drive/root-relative ("C:x", "\x")
        raise PermissionError("absolute paths are not allowed; use a path inside the workspace")
    root = WORKSPACE.resolve()
    target = (root / p).resolve()
    if not target.is_relative_to(root):
        raise PermissionError("path escapes the workspace")
    if target.name.lower().startswith(".env"):
        raise PermissionError(".env files are off limits")
    return target


@tool
def list_files(path: str = ".") -> list[str]:
    """List the files and folders in the user's workspace folder.

    Args:
        path: Folder inside the workspace, relative to its root. Defaults to the root.
    """
    folder = _safe_path(path)
    if not folder.is_dir():
        raise NotADirectoryError(f"'{path}' is not a folder in the workspace")
    return sorted(f"{e.name}/" if e.is_dir() else e.name for e in folder.iterdir())


@tool
def read_file(path: str) -> str:
    """Read a text file from the user's workspace folder. Long files are truncated.

    Args:
        path: File path inside the workspace, relative to its root, e.g. "notes.txt".
    """
    return _safe_path(path).read_text(encoding="utf-8", errors="replace")


@tool
def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web (DuckDuckGo) and return titles, URLs and snippets.

    Args:
        query: What to search for.
        max_results: How many results to return (1-10).
    """
    from ddgs import DDGS  # imported lazily so the rest of Kestrel starts fast

    results = DDGS().text(query, max_results=max(1, min(int(max_results), 10)))
    return [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")} for r in results]
