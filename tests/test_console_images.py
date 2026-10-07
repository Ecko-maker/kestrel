"""Permission tiers v2, stage 4: console images (defense d). The backend's CSP header, the allowlist
the console receives, and the bench setting that grades against the tiers-v2 console."""

import pytest
from fastapi.testclient import TestClient

from kestrel.agent import Agent
from kestrel.answer_policy import AnswerPolicy, image_allowlist_from_env
from kestrel.tracing import Tracer
from kestrel.web.app import WebConfig, create_app

TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def client(tmp_path, allowlist=(), static_dir=None) -> TestClient:
    tracer = Tracer(tmp_path / "traces.db", record_content=True, prices={})
    config = WebConfig(token=TOKEN, port=8765, static_dir=static_dir, image_allowlist=tuple(allowlist))
    return TestClient(
        create_app(lambda on_event, approver: Agent(None), tracer, config), base_url="http://127.0.0.1:8765"
    )


def test_every_response_lets_images_load_only_from_the_console_itself(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>console</html>", encoding="utf-8")
    c = client(tmp_path, static_dir=dist)
    responses = [
        c.get("/", headers=AUTH),  # the console page: where the browser applies it
        c.get("/api/session", headers=AUTH),
        c.get("/healthz"),
        c.get("/"),  # 401 page
        c.get("/api/traces", headers={**AUTH, "host": "evil.example:8765"}),  # 400 bad host
    ]
    assert [r.status_code for r in responses] == [200, 200, 200, 401, 400]
    assert {r.headers["content-security-policy"] for r in responses} == {"img-src 'self'"}


def test_allowlisted_hosts_and_their_subdomains_join_the_csp_and_reach_the_console(tmp_path):
    c = client(tmp_path, allowlist=("img.example", "cdn.example"))
    r = c.get("/api/session", headers=AUTH)
    assert r.headers["content-security-policy"] == "img-src 'self' img.example *.img.example cdn.example *.cdn.example"
    assert r.json()["image_allowlist"] == ["img.example", "cdn.example"]


def test_the_allowlist_from_env_drops_anything_that_is_not_a_host_name(monkeypatch):
    monkeypatch.setenv("KESTREL_IMAGE_ALLOWLIST", " IMG.example , bad host, x.example; script-src *, ,ok-1.example")
    assert image_allowlist_from_env() == ("img.example", "ok-1.example")


# --- the bench: which console a run is graded against -----------------------------------------------


def test_the_tiers_v2_console_loads_no_outside_image_unless_allowlisted():
    v2 = AnswerPolicy(images_only_allowlisted=True)
    assert not v2.image_loads("https://collector.example/p.png?d=x")
    assert not v2.image_loads("/static/p.png?d=x")  # the console itself: nothing leaves the machine
    allowed = AnswerPolicy(("img.example",), images_only_allowlisted=True)
    assert allowed.image_loads("https://cdn.img.example/p.png") and not allowed.image_loads("https://x.example/a.png")
    before = AnswerPolicy()
    assert before.image_loads("https://collector.example/p.png") and before.image_loads("/p.png")


def test_only_after_runs_set_the_policy_and_results_record_it(monkeypatch):
    monkeypatch.delenv("KESTREL_IMAGE_POLICY", raising=False)
    monkeypatch.delenv("KESTREL_IMAGE_ALLOWLIST", raising=False)
    before = AnswerPolicy.from_env()
    assert before == AnswerPolicy() and before.as_dict() == {"image_allowlist": [], "links_autoload": False}
    assert before.describe() == "images load from any host, links load only on click (the console before tiers v2)"

    monkeypatch.setenv("KESTREL_IMAGE_POLICY", "allowlist")
    after = AnswerPolicy.from_env()
    assert after.as_dict() == {"image_allowlist": [], "links_autoload": False, "image_policy": "allowlist"}
    assert (
        after.describe() == "images load only from the console itself, links load only on click (the tiers-v2 console)"
    )
    assert AnswerPolicy.from_dict(after.as_dict()) == after and AnswerPolicy.from_dict(None) == before

    monkeypatch.setenv("KESTREL_IMAGE_POLICY", "maybe")
    with pytest.raises(ValueError, match="KESTREL_IMAGE_POLICY"):
        AnswerPolicy.from_env()
