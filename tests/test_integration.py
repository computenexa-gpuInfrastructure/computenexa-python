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
