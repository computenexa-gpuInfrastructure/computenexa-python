"""0.3.0: Endpoints (a stable HTTPS address for your own server on port 8000) in the client and `cnx endpoints`."""

import json

import httpx
import pytest

import computenexa
from computenexa import Client, Endpoint
from computenexa import cli as cli_module
from tests.test_client import KEY, FakeApi

ACME = {"id": "e1", "name": "Acme Bank", "handle": "e-4f1c9a2b",
        "url": "https://e-4f1c9a2b-endpoint.computenexa-apps.com", "instance": None,
        "created_at": "2026-10-29T10:00:00Z"}


class FakeApi3(FakeApi):
    def __init__(self):
        super().__init__()
        self.endpoints = {"e1": dict(ACME)}

    def handler(self, request):
        path = request.url.path.removeprefix("/v1")
        route = f"{request.method} {path}"
        body = json.loads(request.content) if request.content else None
        if not path.startswith("/endpoints"):
            return super().handler(request)
        self.calls.append((request.method, path, body))
        if route == "GET /endpoints":
            return httpx.Response(200, json=list(self.endpoints.values()))
        if route == "POST /endpoints":
            new = {**ACME, "id": "e2", "name": body["name"], "handle": "e-00000002",
                   "url": "https://e-00000002-endpoint.computenexa-apps.com"}
            self.endpoints["e2"] = new
            return httpx.Response(201, json={**new, "key": "cnx-new-endpoint-key"})
        eid = path.split("/")[2]
        if route == f"GET /endpoints/{eid}":
            return httpx.Response(200, json=self.endpoints[eid])
        if route == f"PATCH /endpoints/{eid}":
            self.endpoints[eid]["name"] = body["name"]
            return httpx.Response(200, json=self.endpoints[eid])
        if route == f"DELETE /endpoints/{eid}":
            self.endpoints.pop(eid)
            return httpx.Response(204)
        if route == f"GET /endpoints/{eid}/key":
            return httpx.Response(200, json={"key": "cnx-endpoint-key"})
        if route == f"POST /endpoints/{eid}/key":
            return httpx.Response(200, json={"key": "cnx-rotated-key"})
        raise AssertionError(route)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(computenexa.client.time, "sleep", lambda s: None)
    monkeypatch.setattr(computenexa.models.time, "sleep", lambda s: None)
    return FakeApi3()


@pytest.fixture
def cnx(api):
    return Client(KEY, base_url="https://api.test/v1", http_client=httpx.Client(transport=httpx.MockTransport(api.handler)))


def test_endpoints(cnx, api):
    (acme,) = cnx.list_endpoints()
    assert isinstance(acme, Endpoint) and (acme.name, acme.handle, acme.instance) == ("Acme Bank", "e-4f1c9a2b", None)
    assert acme.created_at is not None and acme.key is None
    new = cnx.create_endpoint("Globex")
    assert (new.id, new.key) == ("e2", "cnx-new-endpoint-key")
    assert cnx.get_or_create_endpoint("Acme Bank").id == "e1"  # found, not created again
    assert [c for c in api.calls if c[:2] == ("POST", "/endpoints")] == [("POST", "/endpoints", {"name": "Globex"})]
    assert cnx.endpoint_key(acme) == "cnx-endpoint-key" and cnx.rotate_endpoint_key("e1") == "cnx-rotated-key"
    assert cnx.rename_endpoint(acme, "Acme Bank US").name == "Acme Bank US"
    assert cnx.get_endpoint("e1").name == "Acme Bank US"
    cnx.delete_endpoint(new)
    assert api.calls[-1][:2] == ("DELETE", "/endpoints/e2")


def test_launch_for_an_endpoint_and_the_machines_own_address(cnx, api):
    machine = cnx.launch("rtx-4090", app="basic", hours=1, endpoint=cnx.get_endpoint("e1"))
    sent = next(b for m, p, b in api.calls if (m, p) == ("POST", "/instances"))
    assert sent["endpoint_id"] == "e1"
    cnx.launch("rtx-4090", app="basic", hours=1, endpoint="e1")
    assert [b for m, p, b in api.calls if (m, p) == ("POST", "/instances")][-1]["endpoint_id"] == "e1"
    assert machine.endpoint_url is None  # not running yet in the fake
    out = computenexa.Instance.from_api({"id": "cnx-1", "state": "running", "gpu": "rtx-4090",
                                         "endpoint_url": "https://cnx-1-endpoint.x",
                                         "endpoint": {"id": "e1", "name": "Acme Bank", "url": "https://e-1-endpoint.x"}})
    assert (out.endpoint_url, out.endpoint_name) == ("https://cnx-1-endpoint.x", "Acme Bank")


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


def test_cli_endpoints(run, api):
    code, out, _ = run("endpoints")
    assert code == 0 and "Acme Bank" in out and "e-4f1c9a2b-endpoint" in out
    code, out, _ = run("endpoints", "add", "Globex")
    assert code == 0 and "cnx-new-endpoint-key" in out and "--endpoint 'Globex'" in out
    code, out, _ = run("endpoints", "key", "Acme Bank")
    assert code == 0 and out.strip() == "cnx-endpoint-key"
    code, _, err = run("endpoints", "rm", "Globex")
    assert code == 1 and "--yes" in err and "e2" in api.endpoints  # asks first (not a terminal)
    code, out, _ = run("endpoints", "rm", "Globex", "--yes")
    assert code == 0 and "e2" not in api.endpoints
    code, _, err = run("endpoints", "key", "Nobody")
    assert code == 1 and "No endpoint called 'Nobody'" in err
    code, _, err = run("endpoints", "add")
    assert code == 2 and "give a name" in err


def test_cli_launch_for_an_endpoint(run, api):
    code, out, _ = run("launch", "rtx-4090", "--app", "basic", "--hours", "1", "--endpoint", "Acme Bank", "--yes")
    assert code == 0, out
    sent = next(b for m, p, b in api.calls if (m, p) == ("POST", "/instances"))
    assert sent["endpoint_id"] == "e1"
