"""Write tests/fixtures/gcal/*.txt: workspace-mcp 2.3.0's own `get_events` text for the event lists in
tests/fixtures/gcal/cases.json. The notifier's calendar parser (kestrel.notify.calendar) is tested on these files, so
it is checked against the server's real output rather than a copy of its format.

Offline: no Google call, no sign-in, no credentials. It runs the server's undecorated `get_events` with a fake
`service` that returns the case's items, using the installed server's own Python (it isn't in Kestrel's venv):

    & "$env:LOCALAPPDATA\\kestrel\\workspace-mcp-2.3.0\\Scripts\\python.exe" -I scripts\\gcal_goldens.py [--check]

Every address in the cases is an example one (owner@example.com and example.org/.net domains).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "gcal"
OWNER = "owner@example.com"


class _Request:
    def __init__(self, result: dict) -> None:
        self.result = result

    def execute(self) -> dict:
        return self.result


class _Events:
    def __init__(self, case: dict) -> None:
        self.case = case

    def list(self, **params: object) -> _Request:
        result: dict = {"items": self.case["items"]}
        if self.case.get("next_page_token"):
            result["nextPageToken"] = self.case["next_page_token"]
        return _Request(result)


class _Service:
    def __init__(self, case: dict) -> None:
        self.case = case

    def events(self) -> _Events:
        return _Events(self.case)


def render(case: dict) -> str:
    import gcalendar.calendar_tools as tools

    inner = tools.get_events
    while getattr(inner, "__wrapped__", None) is not None:
        inner = inner.__wrapped__
    return asyncio.run(
        inner(
            _Service(case),
            OWNER,
            calendar_id="primary",
            time_min=case["time_min"],
            time_max=case["time_max"],
            max_results=50,
            detailed=True,
            single_events=True,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare instead of writing; exit 1 on a difference")
    args = parser.parse_args()
    import logging

    logging.disable(logging.CRITICAL)
    import importlib.metadata

    version = importlib.metadata.version("workspace-mcp")
    if version != "2.3.0":
        print(f"this is workspace-mcp {version}; the goldens are for 2.3.0")
        return 2
    cases = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))
    stale = []
    for name, case in cases.items():
        text = render(case)
        path = FIXTURES / f"{name}.txt"
        if args.check:
            if not path.exists() or path.read_bytes() != text.encode("utf-8"):
                stale.append(name)
        else:
            path.write_bytes(text.encode("utf-8"))
    if args.check:
        print("goldens up to date" if not stale else f"stale: {', '.join(stale)}")
        return 1 if stale else 0
    print(f"wrote {len(cases)} goldens to {FIXTURES.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
