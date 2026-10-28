"""0.2.0: schedules, usage, receipts, Model API helpers, machine apps and saving; the `cnx` command-line tool."""

import json
import re
from datetime import datetime, timezone

import httpx
import pytest

import computenexa
from computenexa import Client, window
from computenexa import cli as cli_module
from tests.test_client import KEY, FakeApi

SCHEDULE = {"id": "s1", "name": "Weekdays", "project_id": "p0", "family": "chat", "model": None,
            "gpu_slugs": ["l40s", "a40"], "region": "any", "timezone": "America/Los_Angeles",
            "windows": [{"days": [0, 1, 2, 3, 4], "start": "08:00", "end": "18:00"}], "skip_dates": [],
            "lead_minutes": 15, "paused_until": None, "next_window_start": "2026-11-02T15:45:00Z",
            "next_window_end": "2026-11-03T02:00:00Z", "hours_per_month": 217.0, "estimated_cents_per_month": 29729,
            "runs": [{"window_start": "2026-10-30T15:45:00Z", "window_end": "2026-10-31T01:00:00Z", "state": "launched",
                      "instance": "cnx-aa11bb", "attempts": 1, "last_error": None}]}
RECEIPT = {"public_id": "cnx-aa11bb", "label": None, "gpu": "l40s", "gpu_count": 1, "family": "chat", "model": None,
           "region": "us", "requested_at": "2026-10-30T15:45:00Z", "started_at": "2026-10-30T15:46:00Z",
           "ended_at": "2026-10-31T01:00:00Z", "used_minutes": 554.0, "booked_minutes": 555, "charged_cents": 1267,
           "refunded_cents": 2, "net_cents": 1265, "project": "data", "outcome": "expired", "ready_seconds": 60}


class FakeApi2(FakeApi):
    def __init__(self):
        super().__init__()
        self.schedule = dict(SCHEDULE)

    def handler(self, request):
        path = request.url.path.removeprefix("/v1")
        route = f"{request.method} {path}"
        body = json.loads(request.content) if request.content else None
        if route == "GET /instances":
            self.calls.append((request.method, path, body))
            return httpx.Response(200, json=[self._out(i) for i in self.instances])
        if route.startswith(("GET /me/schedules", "POST /me/schedules", "PUT /me/schedules", "DELETE /me/schedules",
                             "GET /me/usage", "GET /me/receipts", "GET /me/model-api", "GET /projects/p0/models")) \
                or re.fullmatch(r"(GET /instances/cnx-\w+/api-key|POST /instances/cnx-\w+/(save|apps/\w+/open))", route):
            self.calls.append((request.method, path, body))
            return self._v020(route, request, body)
        return super().handler(request)

    def _v020(self, route, request, body):
        if route == "GET /me/schedules":
            return httpx.Response(200, json=[self.schedule])
        if route in ("POST /me/schedules", "PUT /me/schedules/s1"):
            self.schedule.update(name=body["name"], family=body["family"], gpu_slugs=body["gpu_slugs"],
                                 windows=body["windows"])
            return httpx.Response(201 if request.method == "POST" else 200, json=self.schedule)
        if route == "POST /me/schedules/s1/pause":
            self.schedule["paused_until"] = body["until"]
            return httpx.Response(200, json=self.schedule)
        if route == "DELETE /me/schedules/s1":
            return httpx.Response(204)
        if route == "GET /me/usage":
            return httpx.Response(200, json={"days": int(request.url.params["days"]), "balance_cents": 9677,
                                             "spend_cents": 1265, "topups_cents": 5000, "sessions": 3, "gpu_hours": 9.2,
                                             "by_gpu": [], "by_image": [], "daily": [], "projects": []})
        if route == "GET /me/receipts":
            return httpx.Response(200, json=[RECEIPT])
        if route == "GET /me/model-api":
            return httpx.Response(200, json={"handle": "u-1a2b3c4d", "base_url": "https://u-1a2b3c4d-api.x/v1",
                                             "instance": "cnx-000000"})
        if route == "GET /projects/p0/models":
            return httpx.Response(200, json=["qwen3.8:27b"] if request.url.params["family"] == "chat" else [])
        if route.endswith("/api-key"):
            return httpx.Response(200, json={"key": "cnx-machinekey"})
        if route.endswith("/open"):
            return httpx.Response(200, json={"url": "https://cnx-000000-chat.x/login?t=once"})
        if route.endswith("/save"):
            return httpx.Response(202, json={"ok": True})
        raise AssertionError(route)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(computenexa.client.time, "sleep", lambda s: None)
    monkeypatch.setattr(computenexa.models.time, "sleep", lambda s: None)
    return FakeApi2()


@pytest.fixture
def cnx(api):
    return Client(KEY, base_url="https://api.test/v1", http_client=httpx.Client(transport=httpx.MockTransport(api.handler)))


def test_window_reads_day_names_and_ranges():
    assert window("mon-fri", "08:00", "18:00") == {"days": [0, 1, 2, 3, 4], "start": "08:00", "end": "18:00"}
    assert window("Sat, Sunday", "10:00", "14:00")["days"] == [5, 6]
    assert window("fri-mon", "09:00", "10:00")["days"] == [0, 4, 5, 6]          # wraps over the weekend
    assert window([2, 0, 2], "09:00", "12:00")["days"] == [0, 2]
    with pytest.raises(ValueError):
        window("someday", "09:00", "10:00")


def test_schedules(cnx, api):
    proj = cnx.create_project("data")
    s = cnx.create_schedule("Weekdays", project=proj, app="chat", gpus=["l40s", "a40"],
                            timezone="America/Los_Angeles", windows=[window("mon-fri", "08:00", "18:00")])
    sent = api.calls[-1][2]
    assert sent == {"name": "Weekdays", "project_id": proj.id, "family": "chat", "model": None,
                    "gpu_slugs": ["l40s", "a40"], "region": "any", "timezone": "America/Los_Angeles",
                    "windows": [{"days": [0, 1, 2, 3, 4], "start": "08:00", "end": "18:00"}], "skip_dates": [],
                    "lead_minutes": 15}
    assert (s.name, s.app, s.gpus, s.paused, s.estimated_cents_per_month) == ("Weekdays", "chat", ["l40s", "a40"],
                                                                              False, 29729)
    assert s.runs[0].instance == "cnx-aa11bb" and s.next_window_start.tzinfo is not None
    until = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    assert cnx.pause_schedule(s, until).paused
    assert api.calls[-1][2] == {"until": "2026-11-09T08:00:00+00:00"}
    assert not cnx.resume_schedule(s).paused and api.calls[-1][2] == {"until": None}
    assert [x.name for x in cnx.list_schedules()] == ["Weekdays"]
    cnx.delete_schedule(s)
    assert api.calls[-1][:2] == ("DELETE", "/me/schedules/s1")
    with pytest.raises(ValueError, match="at least one GPU"):
        cnx.create_schedule("x", project=proj, app="chat", gpus=[], timezone="UTC", windows=[])


def test_usage_receipts_and_model_api(cnx, api):
    u = cnx.usage(days=7)
    assert (u.days, u.spend_cents, u.gpu_hours) == (7, 1265, 9.2)
    r = cnx.receipts(days=7)[0]
    assert (r.instance, r.net_cents, r.outcome, r.app) == ("cnx-aa11bb", 1265, "expired", "chat")
    cnx.receipts(start="2026-10-01", end=datetime(2026, 10, 31, tzinfo=timezone.utc))
    assert api.calls[-1][:2] == ("GET", "/me/receipts")
    m = cnx.model_api()
    assert (m.handle, m.instance) == ("u-1a2b3c4d", "cnx-000000") and m.base_url.endswith("/v1")
    assert cnx.project_models("p0", "chat") == ["qwen3.8:27b"]


def test_machine_api_key_app_link_and_save(cnx, api):
    machine = cnx.launch("rtx-4090", app="modelapi", hours=1)
    assert machine.api_key() == "cnx-machinekey"
    assert machine.app_url("chat").startswith("https://")
    machine.save_project()
    assert api.calls[-1][:2] == ("POST", f"/instances/{machine.id}/save")


# ------------------------------------------------------------------ the command-line tool

@pytest.fixture
def run(api, monkeypatch, capsys):
    monkeypatch.setenv("CNX_API_KEY", KEY)
    real = cli_module.Client
    monkeypatch.setattr(cli_module, "Client", lambda: real(
        KEY, base_url="https://api.test/v1", http_client=httpx.Client(transport=httpx.MockTransport(api.handler))))

    def run(*argv):
        code = cli_module.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return run


def test_cli_reads(run):
    code, out, _ = run("whoami")
    assert code == 0 and "alex@acme.com · Acme (owner, 3 people)" in out and "$96.77" in out
    code, out, _ = run("gpus", "--app", "datascience")
    assert code == 0 and "rtx-4090" in out and "l40s" not in out and "$0.93/h" in out
    code, out, _ = run("gpus", "--json")
    assert [g["slug"] for g in json.loads(out)] == ["rtx-4090", "l40s"]
    code, out, _ = run("apps")
    assert "datascience" in out and "Private Chat" in out
    code, out, _ = run("usage", "--days", "7")
    assert "Last 7 days: 3 sessions, 9.2 GPU-hours, spent $12.65" in out
    code, out, _ = run("schedules")
    assert "Weekdays" in out and "$297.29/month" in out


def test_cli_launch_asks_unless_yes_and_waits(run, api):
    code, _, err = run("launch", "rtx-4090", "--app", "datascience", "--hours", "2")
    assert code == 1 and "--yes" in err and api.instances == {}                # not a terminal: nothing launched
    code, out, _ = run("launch", "rtx-4090", "--app", "datascience", "--hours", "2", "--project", "data",
                       "--create-project", "--label", "nightly", "--yes")
    assert code == 0, out
    (iid,) = api.instances
    assert "Launched" in out and f"{iid} is running" in out and "ssh.computenexa.com" in out
    sent = next(b for m, p, b in api.calls if (m, p) == ("POST", "/instances"))
    assert (sent["duration_minutes"], sent["label"], sent["image_family"]) == (120, "nightly", "datascience")
    code, out, _ = run("ls")
    assert iid in out and "running" in out
    code, out, _ = run("stop", iid, "--yes")
    assert code == 0 and "refunded $1.50" in out


def test_cli_errors_are_one_clear_line(run, api):
    api.launch_error = (402, {"error": "insufficient_credits", "message": "Not enough credits for this duration.",
                              "request_id": "req-9"})
    code, _, err = run("launch", "rtx-4090", "--app", "chat", "--minutes", "30", "--yes")
    assert code == 1 and err.startswith("Error: Not enough credits") and "req-9" in err
    code, _, err = run("projects", "create")
    assert code == 2 and "give a name" in err
