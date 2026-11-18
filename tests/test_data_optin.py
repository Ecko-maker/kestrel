"""kestrel-data is opt-in: off by default, the default agent byte for byte unchanged (decision 35)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from kestrel import data
from kestrel.bench.runner import agent_fingerprint
from kestrel.tools import registry


def test_the_default_agent_is_unchanged() -> None:
    import kestrel.bench.cli
    import kestrel.bench.rca
    import kestrel.bench.stubs  # noqa: F401

    fp = agent_fingerprint("groq", "openai/gpt-oss-120b")
    assert fp["sha"] == "8a8174d8f17d"
    assert (fp["tools"], fp["system_prompt"], fp["tool_count"]) == ("ede3efa38c54", "36d57c7e8a82", 10)
    assert "data" not in fp
    assert not [n for n in registry.tools if n.startswith("data__")]


def test_the_opt_in_agent_has_its_own_fingerprint() -> None:
    pytest.importorskip("duckdb")
    from kestrel.bench.stubs import main_registry

    fp = agent_fingerprint("groq", "openai/gpt-oss-120b", main_registry([], data=True), data=True)
    assert fp["tool_count"] == 15 and fp["sha"] != "8a8174d8f17d"
    assert fp["data"]["tools"][0] == "data__describe_dataset" and fp["data"]["capabilities"] == ["reads_local"]


@pytest.mark.parametrize(
    ("cli", "env", "expected"),
    [
        (None, {}, False),
        (["data"], {}, True),
        (None, {"KESTREL_TOOLS": "data"}, True),
        (None, {"KESTREL_TOOLS": "google, data"}, True),
        (None, {"KESTREL_TOOLS": "datasets"}, False),
    ],
)
def test_requested(cli, env, expected) -> None:
    assert data.requested(cli, env) is expected


def test_the_data_folder_must_not_be_the_workspace(tmp_path: Path) -> None:
    pytest.importorskip("duckdb")
    ws = tmp_path / "workspace"
    with pytest.raises(data.DataConfigError, match="inside the workspace"):
        data.load_settings({"KESTREL_DATA_DIR": str(ws / "datasets")}, workspace=ws)
    settings = data.load_settings({"KESTREL_DATA_DIR": str(tmp_path / "datasets")}, workspace=ws)
    assert settings.folder == (tmp_path / "datasets").resolve()
    assert settings.args[:3] == ["-m", "kestrel.data.server", "serve"]


def test_start_data_does_nothing_without_the_opt_in() -> None:
    from kestrel import start_data

    assert start_data(argparse.Namespace(data=None)) is None
    assert not [n for n in registry.tools if n.startswith("data__")]
