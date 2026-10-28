"""The client against an in-memory stand-in for the API (no network, no money)."""

import json
import re

import httpx
import pytest

import computenexa
from computenexa import (
    AuthenticationError,
    Client,
    GpuLimitReachedError,
    InsufficientCreditsError,
    LaunchFailedError,
    NoCapacityError,
    PermissionDeniedError,
    Project,
    ServerError,
)

KEY = "cnxk_" + "t" * 40


class FakeApi:
    """Just enough of the API: account, catalog, instances (start running after `ticks` reads), projects."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.instances: dict[str, dict] = {}
        self.projects: dict[str, dict] = {}
        self.ticks = 1
        self.fail_next: list[tuple[int, dict]] = []
        self.launch_error: tuple[int, dict] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path.removeprefix("/v1")
        self.calls.append((request.method, path, body))
        assert request.headers["authorization"] == f"Bearer {KEY}"
        if self.fail_next:
            status, payload = self.fail_next.pop(0)
            return httpx.Response(status, json=payload)
        route = f"{request.method} {path}"
        if route == "GET /me":
            return httpx.Response(200, json={"id": "u1", "email": "alex@acme.com", "email_verified": True,
                                             "role": "customer", "max_concurrent_gpus": 4, "balance_cents": 9677,
                                             "organization": {"id": "o1", "name": "Acme", "role": "owner",
                                                              "members": 3}})
        if route == "GET /catalog":
            return httpx.Response(200, json=CATALOG)
        if route == "POST /instances":
            if self.launch_error:
                return httpx.Response(self.launch_error[0], json=self.launch_error[1])
            iid = f"cnx-{len(self.instances):06x}"
            self.instances[iid] = {"id": iid, "state": "requested", "gpu": body["gpu"], "gpu_count": 1,
                                   "image_family": body.get("image_family"), "label": body.get("label"),
                                   "price_per_hour_cents": 93, "charged_cents": 186, "refunded_cents": 0,
                                   "cost_so_far_cents": 0, "ssh_command": None, "_reads": 0}
            return httpx.Response(201, json=self._out(iid))
        m = re.fullmatch(r"(GET|POST) /instances/(cnx-\w+)(?:/(stop|cancel|extend))?", route)
        if m:
            iid, action = m.group(2), m.group(3)
            inst = self.instances[iid]
            if action is None:
                inst["_reads"] += 1
                if inst["state"] == "requested" and inst["_reads"] >= self.ticks:
                    inst["state"], inst["ssh_command"] = "running", f"ssh {iid}@ssh.computenexa.com"
            elif action == "stop":
                inst.update(state="stopping", refunded_cents=150, end_reason="user_stopped")
            elif action == "cancel":
                inst.update(state="stopping", refunded_cents=186, end_reason="user_cancelled")
            elif action == "extend":
                inst["charged_cents"] += 93 * body["minutes"] // 60
            return httpx.Response(200, json=self._out(iid))
        if route == "GET /projects":
            return httpx.Response(200, json=list(self.projects.values()))
        if route == "POST /projects":
            pid = f"p{len(self.projects)}"
            self.projects[pid] = {"id": pid, "name": body["name"], "bytes_used": 0, "files": 0,
                                  "created_at": "2026-10-28T10:00:00Z", "last_used_at": None, "in_use_by": None}
            return httpx.Response(201, json=self.projects[pid])
        return httpx.Response(404, json={"error": "not_found", "message": "Not found.", "request_id": "r404"})

    def _out(self, iid):
        return {k: v for k, v in self.instances[iid].items() if not k.startswith("_")}


CATALOG = {
    "region": "any",
    "gpus": [
        {"slug": "rtx-4090", "display_name": "NVIDIA RTX 4090", "vram_gb": 24, "available": True,
         "price_per_hour_cents": 93, "billing_unit": "minute", "families": {"chat": "x", "datascience": "y"},
         "regions": ["us"]},
        {"slug": "l40s", "display_name": "NVIDIA L40S", "vram_gb": 48, "available": True,
         "price_per_hour_cents": 137, "billing_unit": "minute", "families": {"chat": "x"}, "regions": ["us"]},
        {"slug": "h100-80gb", "display_name": "NVIDIA H100 80GB", "vram_gb": 80, "available": False,
         "price_per_hour_cents": None, "billing_unit": "minute", "families": {"chat": "x"}, "regions": []},
    ],
    "families": [{"family": "chat", "display_name": "Private Chat", "summary": "Chat", "needs_ssh_key": False},
                 {"family": "datascience", "display_name": "Data Science", "summary": "Jupyter"}],
    "launch_models": {"chat": {"models": [{"name": "small"}, {"name": "big"}], "required": False,
                               "recommended": [[48, "big"], [24, "small"]]}},
}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(computenexa.client.time, "sleep", lambda s: None)
    monkeypatch.setattr(computenexa.models.time, "sleep", lambda s: None)
    return FakeApi()


@pytest.fixture
def cnx(api):
    return Client(KEY, base_url="https://api.test/v1", http_client=httpx.Client(transport=httpx.MockTransport(api.handler)))


def test_needs_a_real_looking_key(monkeypatch):
    monkeypatch.delenv("CNX_API_KEY", raising=False)
    with pytest.raises(computenexa.ComputeNexaError, match="No API key"):
        Client()
    with pytest.raises(computenexa.ComputeNexaError, match="start with cnxk_"):
        Client("sk-something")
    assert KEY not in repr(Client(KEY))


def test_account_and_catalog(cnx):
    me = cnx.account()
    assert (me.email, me.balance, me.organization.name, me.organization.role) == ("alex@acme.com", 96.77, "Acme",
                                                                                  "owner")
    c = cnx.catalog()
    assert [g.slug for g in c.available()] == ["rtx-4090", "l40s"]          # cheapest first, sold out left out
    assert [g.slug for g in c.available(app="datascience")] == ["rtx-4090"]
    assert c.apps["chat"].recommended_model(48) == "big" and c.apps["chat"].recommended_model(24) == "small"
    assert c.apps["chat"].recommended_model(16) is None
    assert c.gpu("l40s").vram_gb == 48


def test_launch_sends_the_right_body_and_waits_until_running(cnx, api):
    proj = cnx.get_or_create_project("titanic")
    assert cnx.get_or_create_project("Titanic").id == proj.id                 # found, not created twice
    api.ticks = 3
    seen = []
    m = cnx.launch("rtx-4090", app="datascience", hours=2, project=proj, label="nightly")
    assert api.calls[-1][2] == {"gpu": "rtx-4090", "duration_minutes": 120, "gpu_count": 1, "region": "any",
                                "image_family": "datascience", "label": "nightly", "project_id": proj.id}
    m.wait_until_running(on_progress=lambda i: seen.append(i.state))
    assert seen == ["requested", "requested", "running"] and m.ssh_command.endswith("@ssh.computenexa.com")
    m.extend(minutes=60)
    assert m.charged_cents == 186 + 93
    m.stop()
    assert (m.state, m.refunded_cents) == ("stopping", 150)


def test_the_with_block_always_stops_the_gpu(cnx, api):
    with pytest.raises(ZeroDivisionError), cnx.gpu("rtx-4090", app="chat", minutes=30) as m:
        assert m.is_running
        raise ZeroDivisionError("the user's code fails")
    assert api.instances[m.id]["state"] == "stopping"                          # …and the GPU still stopped
    api.ticks = 99                                                            # this one never gets going
    with cnx.gpu("rtx-4090", app="chat", minutes=30, wait=False) as m2:
        pass
    assert api.instances[m2.id]["end_reason"] == "user_cancelled"              # still starting: cancelled, full refund


def test_a_machine_that_fails_to_start_raises(cnx, api):
    m = cnx.launch("rtx-4090", app="chat", hours=1)
    api.instances[m.id]["state"] = "failed"
    api.instances[m.id]["end_reason"] = "no_capacity"
    with pytest.raises(LaunchFailedError, match="no_capacity"):
        m.wait_until_running()


@pytest.mark.parametrize("status, body, error", [
    (402, {"error": "insufficient_credits", "message": "Not enough credits for this duration."},
     InsufficientCreditsError),
    (409, {"error": "no_capacity", "message": "No RTX 4090 free."}, NoCapacityError),
    (409, {"error": "gpu_limit_reached", "message": "Your team can run 4 GPU(s) at a time."}, GpuLimitReachedError),
    (403, {"error": "forbidden", "message": "This API key can't do that."}, PermissionDeniedError),
    (401, {"error": "not_signed_in", "message": "This API key isn't valid."}, AuthenticationError),
])
def test_refusals_become_specific_errors_with_their_details(cnx, api, status, body, error):
    api.launch_error = (status, {**body, "request_id": "req-1"})
    with pytest.raises(error) as e:
        cnx.launch("rtx-4090", app="chat", hours=1)
    assert (e.value.status, e.value.code, e.value.request_id) == (status, body["error"], "req-1")
    assert body["message"] in str(e.value)


def test_reads_are_retried_but_a_launch_is_never_sent_twice(cnx, api):
    api.fail_next = [(503, {"error": "unavailable", "message": "busy"}), (429, {"error": "rate_limited",
                                                                               "message": "slow down"})]
    assert cnx.account().email == "alex@acme.com"                              # retried through both
    assert sum(1 for m, p, _ in api.calls if p == "/me") == 3
    api.fail_next = [(503, {"error": "unavailable", "message": "busy"})]
    with pytest.raises(ServerError):
        cnx.launch("rtx-4090", app="chat", hours=1)                           # a write: not retried on 5xx
    assert sum(1 for m, p, _ in api.calls if (m, p) == ("POST", "/instances")) == 1
    assert api.instances == {}


def test_arguments_are_checked_before_calling(cnx, api):
    with pytest.raises(ValueError, match="app=.*image="):
        cnx.launch("rtx-4090", hours=1)
    with pytest.raises(ValueError, match="minutes=.*hours="):
        cnx.launch("rtx-4090", app="chat")
    with pytest.raises(ValueError, match="at least 1 minute"):
        cnx.launch("rtx-4090", app="chat", minutes=0)
    assert api.calls == []


def test_project_objects_or_ids(cnx, api):
    p = cnx.create_project("data")
    assert isinstance(p, Project)
    cnx.launch("rtx-4090", app="chat", hours=1, project=p.id)
    assert api.calls[-1][2]["project_id"] == p.id
