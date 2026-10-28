"""End to end against a real ComputeNexa API. Skipped unless CNX_TEST_API_URL and CNX_TEST_API_KEY are set.

Meant for a local or staging API whose GPUs are fake: it launches and stops a machine. Never point it at
production with a real key unless you mean to spend a few cents.
"""

import os

import pytest

from computenexa import Client, ComputeNexaError, PermissionDeniedError

URL, KEY = os.environ.get("CNX_TEST_API_URL"), os.environ.get("CNX_TEST_API_KEY")
READ_KEY = os.environ.get("CNX_TEST_READ_KEY")
pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not (URL and KEY), reason="set CNX_TEST_API_URL and CNX_TEST_API_KEY")]


def test_the_whole_journey():
    cnx = Client(KEY, base_url=URL)
    me = cnx.account()
    before = me.balance_cents
    catalog = cnx.catalog()
    gpu = next(g for g in catalog.available(app="datascience") if g.billing_unit == "minute")
    assert "datascience" in catalog.apps and cnx.image_contents("datascience")["images"]

    try:
        project = cnx.get_or_create_project("sdk-integration")
    except ComputeNexaError as e:  # a local API without project storage
        if e.code != "not_configured":
            raise
        project = None
    with cnx.gpu(gpu.slug, app="datascience", minutes=10, project=project, label="sdk test", timeout=300) as m:
        assert m.is_running and m.ssh_command
        assert m.id in {i.id for i in cnx.list_instances(active=True)}
        m.extend(minutes=5)
    m.refresh()
    assert m.state in ("stopping", "terminated") and m.refunded_cents > 0
    assert cnx.account().balance_cents < before                       # the minutes used were paid
    assert any(e.instance == m.id for e in cnx.ledger(limit=20))


@pytest.mark.skipif(not READ_KEY, reason="set CNX_TEST_READ_KEY for the look-only check")
def test_a_look_only_key_reads_but_cannot_launch():
    cnx = Client(READ_KEY, base_url=URL)
    assert cnx.catalog().gpus
    with pytest.raises(PermissionDeniedError):
        cnx.launch("rtx-4090", app="basic", minutes=10)


def test_usage_receipts_model_api_and_schedules():
    from computenexa import NotFoundError, window

    cnx = Client(KEY, base_url=URL)
    assert cnx.usage(days=7).days == 7
    assert isinstance(cnx.receipts(days=7), list)
    try:
        assert cnx.model_api().base_url.endswith("/v1")
    except NotFoundError:  # an API without the Model API feature
        pass
    try:
        project = cnx.get_or_create_project("sdk-schedules")
    except ComputeNexaError as e:
        if e.code != "not_configured":
            raise
        pytest.skip("this API has no project storage, which schedules need")
    gpu = cnx.catalog().available(app="chat")[0].slug
    s = cnx.create_schedule("SDK test", project=project, app="chat", gpus=[gpu], timezone="UTC",
                            windows=[window("sat", "03:00", "04:00")])
    try:
        assert s.paused is False and s.next_window_start is not None
    finally:
        cnx.delete_schedule(s)


def test_the_command_line_tool():
    import subprocess
    import sys

    env = {**os.environ, "CNX_API_KEY": KEY, "CNX_API_URL": URL}

    def cnx(*args):
        r = subprocess.run([sys.executable, "-m", "computenexa.cli", *args], check=False, env=env,
                           capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, r.stderr
        return r.stdout

    assert "Balance" in cnx("whoami")
    gpu = json_list(cnx("gpus", "--app", "basic", "--json"))[0]["slug"]
    out = cnx("launch", gpu, "--app", "basic", "--minutes", "10", "--label", "cli test", "--yes", "--timeout", "300")
    machine = out.split("Launched ")[1].split(":")[0]
    assert f"{machine} is running" in out
    assert machine in cnx("ls")
    assert "refunded" in cnx("stop", machine, "--yes")


def json_list(text):
    import json
    return json.loads(text)
