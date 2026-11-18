"""Scripted analysts for the `rca` split: they validate the checks, not a model (no model calls).

Each plays one task at a time (the setup calls begin()). It calls decompose_change on the planted cause's own
dimensions (truth.json; product for no-change), reads what the tool printed from the conversation, and writes an
answer in the contract's format, quoting only printed numbers. Then each breaks one rule:
- right-analyst: nothing; it must pass every check on every task (which also proves each task is passable);
- red-herring-analyst: adds the red-herring product as a high-confidence hypothesis -> red_herring_rejected fails;
- invented-number-analyst: adds a number no tool printed -> numbers_match fails;
- false-cause-analyst: claims the top candidate as a high-confidence cause -> on no-change, no_change fails;
- unparseable-analyst: the right content as prose, no 'Hypotheses:' section -> every hypothesis check fails.
"""

from __future__ import annotations

import json
import re
from typing import Any

from kestrel.bench.rca import DATASET, LABELS, RcaTask, dataset_for

MODELS = (
    "right-analyst",
    "red-herring-analyst",
    "invented-number-analyst",
    "false-cause-analyst",
    "unparseable-analyst",
)
DIMS = {
    "customer": ["customer"],
    "price": ["product"],
    "stockout": ["product", "region"],
    "channel-shift": ["channel"],
    "none": ["product"],
}
INVENTED = ("98,765.43", "87,654.32", "76,543.21")
_GAP = re.compile(r"Gap from expected: (-?[\d,]+\.\d+) \((-?[\d.]+%) of expected\)")
_STAMP = re.compile(r"\[E(\d+)\] data__decompose_change")


def _call(n: int, args: dict[str, Any]) -> dict[str, Any]:
    call = {
        "id": f"call-{n}",
        "type": "function",
        "function": {"name": "data__decompose_change", "arguments": json.dumps(args)},
    }
    return {"role": "assistant", "content": None, "tool_calls": [call]}


def _say(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def _rows(text: str) -> dict[str, list[str]]:
    out = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 7 and cells[5] in LABELS:
            out[cells[0].split(" = ", 1)[-1]] = cells
    return out


class ScriptedAnalyst:
    supports_streaming = False

    def __init__(self, name: str):
        if name not in MODELS:
            raise ValueError(f"unknown scripted analyst {name!r}; choose from {', '.join(MODELS)}")
        self.name = name
        self.model = name
        self.truth: dict[str, Any] = {}
        self.dims: list[str] = []

    def begin(self, rt: RcaTask) -> None:
        _, self.truth, _, _ = dataset_for(rt.meta)
        self.dims = list(DIMS[self.truth["kind"]])
        if self.name == "red-herring-analyst" and "product" not in self.dims:
            self.dims.append("product")  # it names the red herring only after a tool showed it

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict[str, Any]:
        last_user = max(i for i, m in enumerate(messages) if m.get("role") == "user")
        results = [str(m.get("content") or "") for m in messages[last_user:] if m.get("role") == "tool"]
        if len(results) < len(self.dims):
            dim = self.dims[len(results)]
            return _call(
                len(results),
                {
                    "dataset": DATASET,
                    "metric": "revenue",
                    "period_a": self.truth["period_a"],
                    "period_b": self.truth["period_b"],
                    "by": dim,
                },
            )
        return _say(self.answer(results))

    def answer(self, results: list[str]) -> str:
        t = self.truth
        ids = [int(m.group(1)) for r in results if (m := _STAMP.search(r))]
        tables = [_rows(r) for r in results]
        gap = _GAP.search(results[0])
        pct = gap.group(2) if gap else "n/a"
        first = ids[0]
        finding = (
            f"Finding: revenue for {t['period_b']} was {pct.lstrip('-')} from its seasonal expected value (E{first})."
        )
        herring = t["red_herring"]["value"]
        herring_row = next((tb[herring] for tb in tables if herring in tb), None)
        # it rejects only what a tool showed it: a name it never saw would be invented (numbers_match: "K-1000")
        rejected = (
            f"- product {herring} (E{first}: share of gap {herring_row[2]}, a one-week dip)"
            if herring_row
            else "- nothing else stood out in the evidence"
        )
        hyps: list[str] = []
        if t["planted"]:
            kind, ents = t["kind"], t["entities"]
            row = tables[0][ents[0]]
            phrase = {
                "customer": f"Customer {ents[0]} stopped ordering",
                "price": f"A price rise on product {ents[0]} cut its sales",
                "stockout": f"A stockout of product {ents[0]} in region {ents[-1]}",
                "channel-shift": "A channel shift: sales moved from retail to online at the lower online price",
            }[kind]
            cite = ", ".join(f"E{i}" for i in ids)
            hyps.append(
                f"1. {phrase}. Confidence: {row[5]}.\n   Evidence: {cite} (share of gap {row[2]}, effect size "
                f"z {row[3]}, consistent in {row[4]})."
            )
        if self.name == "red-herring-analyst":
            hyps.append(f"{len(hyps) + 1}. Product {herring} dipped. Confidence: high.\n   Evidence: E{first}.")
        if self.name == "false-cause-analyst":
            top_name = next(iter(tables[0]))
            hyps = [f"1. The {self.dims[0]} {top_name} caused the drop. Confidence: high.\n   Evidence: E{first}."]
        if self.name == "invented-number-analyst":
            known = " ".join(results)
            number = next(n for n in INVENTED if n not in known)
            finding += f" That is about {number} below plan."
        if hyps:
            body = "Hypotheses:\n" + "\n".join(hyps)
        else:
            body = "Hypotheses: none, no cause found; the total change is within normal variation."
        text = f"{finding}\n\n{body}\n\nRejected:\n{rejected}\n\nNot checked: returns and refunds (not in the dataset)."
        if self.name == "unparseable-analyst":
            text = text.replace("Hypotheses:", "My view").replace("Rejected:", "Also")
        return text
