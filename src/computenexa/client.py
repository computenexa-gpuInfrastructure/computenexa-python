"""The ComputeNexa API client."""

from __future__ import annotations

import os
import random
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

import httpx

from . import __version__
from .errors import APIConnectionError, ComputeNexaError, NotFoundError, RateLimitError, ServerError, from_response
from .models import (
    ACTIVE,
    STARTING,
    Account,
    Catalog,
    Instance,
    LedgerEntry,
    ModelApiAddress,
    Project,
    Receipt,
    Schedule,
    SshKey,
    Usage,
)

DEFAULT_BASE_URL = "https://api.computenexa.com/v1"
READS = ("GET", "HEAD")


class Client:
    """A ComputeNexa API client.

        cnx = Client()                         # reads CNX_API_KEY (and CNX_API_URL) from the environment
        cnx = Client(api_key="cnxk_...")

    Create keys in the dashboard under API keys. A key acts as the person who created it and spends their team's
    credits. Look-only keys can read; "launch and stop" keys can also launch, extend and stop.
    """

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None, timeout: float = 30.0,
                 max_retries: int = 3, http_client: httpx.Client | None = None):
        key = api_key or os.environ.get("CNX_API_KEY")
        if not key:
            raise ComputeNexaError("No API key: pass api_key=... or set CNX_API_KEY. Create one in the dashboard "
                                   "under API keys.")
        if not key.startswith("cnxk_"):
            raise ComputeNexaError("That isn't a ComputeNexa API key: they start with cnxk_.")
        self.base_url = (base_url or os.environ.get("CNX_API_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.max_retries = max_retries
        self._http = http_client or httpx.Client(timeout=timeout)
        self._headers = {"Authorization": f"Bearer {key}", "User-Agent": f"computenexa-python/{__version__}"}

    def __repr__(self) -> str:  # never the key
        return f"Client(base_url={self.base_url!r})"

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Client:  # noqa: PYI034 (typing.Self needs Python 3.11; we support 3.9)
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ HTTP

    def _request(self, method: str, path: str, *, json: Any = None, params: dict | None = None) -> Any:
        """One API call. Reads are retried on 429, 5xx and connection errors; writes only on 429 (refused before
        anything happened), so a launch is never sent twice."""
        attempt = 0
        while True:
            try:
                r = self._http.request(method, f"{self.base_url}{path}", json=json, params=params,
                                       headers=self._headers)
            except httpx.HTTPError as e:
                if method in READS and attempt < self.max_retries:
                    attempt += 1
                    self._backoff(attempt)
                    continue
                raise APIConnectionError(f"Couldn't reach the ComputeNexa API: {e}") from e
            if r.status_code < 400:
                return r.json() if r.content else None
            try:
                body = r.json()
            except ValueError:
                body = None
            err = from_response(r.status_code, body)
            retryable = isinstance(err, RateLimitError) or (isinstance(err, ServerError) and method in READS)
            if retryable and attempt < self.max_retries:
                attempt += 1
                self._backoff(attempt, r.headers.get("retry-after"))
                continue
            raise err

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None = None) -> None:
        try:
            wait = float(retry_after) if retry_after else 0.0
        except ValueError:
            wait = 0.0
        time.sleep(max(wait, min(30.0, 0.5 * 2 ** attempt + random.random())))

    # ------------------------------------------------------------------ account

    def account(self) -> Account:
        """Who this key acts as, their team, its balance and GPU limit."""
        return Account.from_api(self._request("GET", "/me"))

    def balance_cents(self) -> int:
        """The team's balance in US cents."""
        return int(self._request("GET", "/balance")["balance_cents"])

    def ledger(self, limit: int = 50) -> list[LedgerEntry]:
        """Charges, refunds and top-ups, newest first."""
        return [LedgerEntry.from_api(e) for e in self._request("GET", "/ledger", params={"limit": limit})]

    # ------------------------------------------------------------------ catalog

    def catalog(self, region: str | None = None) -> Catalog:
        """GPUs with live prices and stock, and the apps and models they run. `region`: "us" or "ca"."""
        return Catalog.from_api(self._request("GET", "/catalog", params={"region": region} if region else None))

    def image_contents(self, app: str) -> dict:
        """What's installed in an app's images: Ubuntu, CUDA, Python and every package with its version."""
        return self._request("GET", f"/images/{app}")

    # ------------------------------------------------------------------ instances

    def launch(self, gpu: str, *, app: str | None = None, image: str | None = None, minutes: int | None = None,
               hours: float | None = None, gpu_count: int = 1, region: str = "any", label: str | None = None,
               model: str | None = None, project: Project | str | None = None) -> Instance:
        """Launch a GPU. It's charged now for the booked time; unused time is refunded when it stops early, all of
        it if it can't start.

            cnx.launch("rtx-4090", app="datascience", hours=2, project=proj)

        `app`: "basic", "datascience", "chat", "imagegen", "modelapi" or "voice" (or `image=` an exact image slug).
        `model`: for apps that take one; left out = the recommended model for this GPU.
        """
        if (app is None) == (image is None):
            raise ValueError("Give exactly one of app=... or image=...")
        body: dict[str, Any] = {"gpu": gpu, "duration_minutes": _minutes(minutes, hours), "gpu_count": gpu_count,
                                "region": region}
        if app is not None:
            body["image_family"] = app
        if image is not None:
            body["image"] = image
        if label is not None:
            body["label"] = label
        if model is not None:
            body["model"] = model
        if project is not None:
            body["project_id"] = project.id if isinstance(project, Project) else str(project)
        return Instance.from_api(self._request("POST", "/instances", json=body), self)

    def get_instance(self, instance_id: str) -> Instance:
        return Instance.from_api(self._request("GET", f"/instances/{instance_id}"), self)

    def list_instances(self, *, active: bool | None = None, everyone: bool = False, limit: int = 50) -> list[Instance]:
        """Your machines, newest first. `active=True` only starting/running ones, `False` only ended ones.
        `everyone=True`: the whole team's (owners and admins)."""
        params: dict[str, Any] = {"limit": limit}
        if active is not None:
            params["status"] = "active" if active else "ended"
        if everyone:
            params["whose"] = "everyone"
        return [Instance.from_api(d, self) for d in self._request("GET", "/instances", params=params)]

    def extend_instance(self, instance_id: str, *, minutes: int | None = None, hours: float | None = None) -> Instance:
        return Instance.from_api(self._request("POST", f"/instances/{instance_id}/extend",
                                               json={"minutes": _minutes(minutes, hours)}), self)

    def stop_instance(self, instance_id: str) -> Instance:
        """End it now: stop if running, cancel if still starting (full refund). An ended one is returned as is."""
        current = self.get_instance(instance_id)
        if current.state == "running":
            return Instance.from_api(self._request("POST", f"/instances/{instance_id}/stop"), self)
        if current.state in STARTING:
            return Instance.from_api(self._request("POST", f"/instances/{instance_id}/cancel"), self)
        return current

    @contextmanager
    def gpu(self, gpu: str, *, wait: bool = True, timeout: float = 900, **launch: Any) -> Iterator[Instance]:
        """Launch a GPU for the length of a `with` block, then stop it, even if your code fails:

            with cnx.gpu("rtx-4090", app="datascience", hours=2) as machine:
                print(machine.ssh_command)
                ...                      # your work
            # stopped here; unused time refunded

        `wait=True` (default) returns once it's running. Other arguments are those of `launch`.
        """
        machine = self.launch(gpu, **launch)
        try:
            if wait:
                machine.wait_until_running(timeout=timeout)
            yield machine
        finally:
            try:
                if machine.refresh().state in ACTIVE:
                    machine.stop()
            except NotFoundError:
                pass

    # ------------------------------------------------------------------ projects

    def list_projects(self) -> list[Project]:
        return [Project.from_api(p) for p in self._request("GET", "/projects")]

    def create_project(self, name: str) -> Project:
        """An empty project; launch with `project=` to restore it into ~/work and save it back automatically."""
        return Project.from_api(self._request("POST", "/projects", json={"name": name}))

    def get_or_create_project(self, name: str) -> Project:
        for p in self.list_projects():
            if p.name.lower() == name.lower():
                return p
        return self.create_project(name)

    def delete_project(self, project: Project | str) -> None:
        """Delete a project and all its files (refused while a GPU uses it)."""
        self._request("DELETE", f"/projects/{project.id if isinstance(project, Project) else project}")

    def project_models(self, project: Project | str, app: str) -> list[str]:
        """Models already saved in a project for an app ("chat", "imagegen", "modelapi", "voice"): a launch with
        that model and project skips downloading it."""
        pid = project.id if isinstance(project, Project) else project
        return list(self._request("GET", f"/projects/{pid}/models", params={"family": app}))

    # ------------------------------------------------------------------ machines: apps, keys, saving

    def get_instance_api_key(self, instance_id: str) -> str:
        """A Model API or Voice Agent machine's own API key (machines you launched). It stops working when the
        machine stops."""
        return self._request("GET", f"/instances/{instance_id}/api-key")["key"]

    def open_app_url(self, instance_id: str, app: str) -> str:
        """A one-time, signed-in link to one of the machine's browser apps (machines you launched)."""
        return self._request("POST", f"/instances/{instance_id}/apps/{app}/open")["url"]

    def save_project(self, instance_id: str) -> None:
        """Ask a running machine with a project to save it now (within about 30 s)."""
        self._request("POST", f"/instances/{instance_id}/save")

    def model_api(self) -> ModelApiAddress:
        """Your team's stable Model API address (the same on every launch; it points at the newest running Model
        API machine)."""
        return ModelApiAddress.from_api(self._request("GET", "/me/model-api"))

    # ------------------------------------------------------------------ usage and receipts

    def usage(self, days: int = 30) -> Usage:
        """Your GPU use and spending over the last `days` (1-365), with breakdowns by GPU, app, day and project."""
        return Usage.from_api(self._request("GET", "/me/usage", params={"days": days}))

    def receipts(self, days: int | None = 30, *, start: date | datetime | str | None = None,
                 end: date | datetime | str | None = None) -> list[Receipt]:
        """Your finished sessions and what each cost: the last `days`, or between `start` and `end`."""
        if start is not None or end is not None:
            params = {k: v.isoformat() if hasattr(v, "isoformat") else v
                      for k, v in (("start", start), ("end", end)) if v is not None}
        else:
            params = {"days": days}
        return [Receipt.from_api(r) for r in self._request("GET", "/me/receipts", params=params)]

    # ------------------------------------------------------------------ schedules

    def list_schedules(self) -> list[Schedule]:
        return [Schedule.from_api(s) for s in self._request("GET", "/me/schedules")]

    def create_schedule(self, name: str, *, project: Project | str, app: str, gpus: list[str], timezone: str,
                        windows: list[dict], model: str | None = None, region: str = "any",
                        skip_dates: list[date | str] | None = None, lead_minutes: int = 15) -> Schedule:
        """A GPU that starts by itself before each weekly window and stops at its end, with a project:

            cnx.create_schedule("Weekdays", project=proj, app="chat", gpus=["l40s", "a40"],
                                timezone="America/Los_Angeles", windows=[window("mon-fri", "08:00", "18:00")])

        `gpus`: acceptable GPUs in order of preference (the first one free is used). Each window is charged like a
        launch of that length."""
        return Schedule.from_api(self._request("POST", "/me/schedules", json=_schedule_body(
            name, project, app, gpus, timezone, windows, model, region, skip_dates, lead_minutes)))

    def update_schedule(self, schedule: Schedule | str, *, name: str, project: Project | str, app: str,
                        gpus: list[str], timezone: str, windows: list[dict], model: str | None = None,
                        region: str = "any", skip_dates: list[date | str] | None = None,
                        lead_minutes: int = 15) -> Schedule:
        """Replace a schedule's settings (send them all, as for create_schedule)."""
        sid = schedule.id if isinstance(schedule, Schedule) else schedule
        return Schedule.from_api(self._request("PUT", f"/me/schedules/{sid}", json=_schedule_body(
            name, project, app, gpus, timezone, windows, model, region, skip_dates, lead_minutes)))

    def pause_schedule(self, schedule: Schedule | str, until: datetime | str) -> Schedule:
        """No machine starts before `until`. A machine already running keeps running."""
        sid = schedule.id if isinstance(schedule, Schedule) else schedule
        value = until.isoformat() if isinstance(until, datetime) else until
        return Schedule.from_api(self._request("POST", f"/me/schedules/{sid}/pause", json={"until": value}))

    def resume_schedule(self, schedule: Schedule | str) -> Schedule:
        sid = schedule.id if isinstance(schedule, Schedule) else schedule
        return Schedule.from_api(self._request("POST", f"/me/schedules/{sid}/pause", json={"until": None}))

    def delete_schedule(self, schedule: Schedule | str) -> None:
        """No more machines start; one running now keeps running until its window ends (or you stop it)."""
        self._request("DELETE", f"/me/schedules/{schedule.id if isinstance(schedule, Schedule) else schedule}")

    # ------------------------------------------------------------------ SSH keys

    def list_ssh_keys(self) -> list[SshKey]:
        return [SshKey.from_api(k) for k in self._request("GET", "/ssh-keys")]

    def add_ssh_key(self, name: str, public_key: str) -> SshKey:
        """Add a public key (e.g. the contents of ~/.ssh/id_ed25519.pub). Never a private key."""
        return SshKey.from_api(self._request("POST", "/ssh-keys", json={"name": name, "public_key": public_key}))

    def delete_ssh_key(self, key: SshKey | str) -> None:
        self._request("DELETE", f"/ssh-keys/{key.id if isinstance(key, SshKey) else key}")


def _schedule_body(name, project, app, gpus, timezone, windows, model, region, skip_dates, lead_minutes) -> dict:
    if not gpus:
        raise ValueError("Give at least one GPU in gpus=[...].")
    return {"name": name, "project_id": project.id if isinstance(project, Project) else str(project), "family": app,
            "model": model, "gpu_slugs": list(gpus), "region": region, "timezone": timezone, "windows": list(windows),
            "skip_dates": [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in skip_dates or []],
            "lead_minutes": lead_minutes}


def _minutes(minutes: int | None, hours: float | None) -> int:
    if (minutes is None) == (hours is None):
        raise ValueError("Give exactly one of minutes=... or hours=...")
    value = int(minutes) if minutes is not None else round(float(hours) * 60)  # type: ignore[arg-type]
    if value < 1:
        raise ValueError("The duration must be at least 1 minute.")
    return value
