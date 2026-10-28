"""What the API returns, as Python objects. Every object keeps the full reply in `.raw` (fields added to the API
later are there before this library knows them)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .errors import LaunchFailedError, WaitTimeoutError

if TYPE_CHECKING:
    from .client import Client

STARTING = ("requested", "provisioning")
ACTIVE = ("requested", "provisioning", "running")
ENDED = ("terminated", "failed")


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass
class Organization:
    id: str
    name: str
    role: str        # your role in it: owner | admin | member
    members: int


@dataclass
class Account:
    """Who the API key acts as, and their team."""

    id: str
    email: str
    balance_cents: int          # the team's balance
    max_concurrent_gpus: int    # the team's GPU limit
    organization: Organization
    raw: dict = field(repr=False)

    @property
    def balance(self) -> float:
        """The team's balance in US dollars."""
        return self.balance_cents / 100

    @classmethod
    def from_api(cls, d: dict) -> Account:
        o = d.get("organization") or {}
        return cls(id=d["id"], email=d["email"], balance_cents=d["balance_cents"],
                   max_concurrent_gpus=d["max_concurrent_gpus"],
                   organization=Organization(id=o.get("id", ""), name=o.get("name", ""), role=o.get("role", ""),
                                             members=o.get("members", 1)), raw=d)


@dataclass
class Gpu:
    """A GPU in the catalog."""

    slug: str                   # what you launch with, e.g. "rtx-4090"
    name: str                   # e.g. "NVIDIA RTX 4090"
    vram_gb: int
    available: bool
    price_per_hour_cents: int | None   # None when not for sale right now
    billing_unit: str           # "minute", or "hour" (billed per started hour)
    apps: list[str]             # apps (image families) it runs: "chat", "datascience", …
    regions: list[str]
    raw: dict = field(repr=False)

    @classmethod
    def from_api(cls, d: dict) -> Gpu:
        return cls(slug=d["slug"], name=d["display_name"], vram_gb=d["vram_gb"], available=d["available"],
                   price_per_hour_cents=d.get("price_per_hour_cents"), billing_unit=d.get("billing_unit", "minute"),
                   apps=sorted((d.get("families") or {}).keys()), regions=list(d.get("regions") or []), raw=d)


@dataclass
class App:
    """Something a GPU can start with (an image family): "basic", "datascience", "chat", "imagegen", "modelapi",
    "voice"."""

    name: str                   # the value for `app=` / image_family
    display_name: str           # as on the launch page
    summary: str
    needs_ssh_key: bool
    models: list[dict]          # models offered (chat, imagegen, modelapi, voice); [] for the others
    model_required: bool
    recommended: list[tuple[int, str]]  # [(min GPU memory in GB, model)], first match wins

    def recommended_model(self, vram_gb: int) -> str | None:
        """Our pick for a GPU with this much memory, or None."""
        return next((m for min_gb, m in self.recommended if vram_gb >= min_gb), None)


@dataclass
class Catalog:
    region: str
    gpus: list[Gpu]
    apps: dict[str, App]
    raw: dict = field(repr=False)

    def gpu(self, slug: str) -> Gpu:
        for g in self.gpus:
            if g.slug == slug:
                return g
        raise KeyError(f"no GPU {slug!r} in the catalog; known: {', '.join(g.slug for g in self.gpus)}")

    def available(self, app: str | None = None) -> list[Gpu]:
        """GPUs for sale right now, cheapest first; only those that run `app` if given."""
        found = [g for g in self.gpus if g.available and g.price_per_hour_cents is not None
                 and (app is None or app in g.apps)]
        return sorted(found, key=lambda g: g.price_per_hour_cents or 0)

    @classmethod
    def from_api(cls, d: dict) -> Catalog:
        models = d.get("launch_models") or {}
        apps = {}
        for f in d.get("families") or []:
            m = models.get(f["family"]) or {}
            apps[f["family"]] = App(
                name=f["family"], display_name=f.get("display_name", f["family"]), summary=f.get("summary", ""),
                needs_ssh_key=f.get("needs_ssh_key", True), models=list(m.get("models") or []),
                model_required=bool(m.get("required")),
                recommended=[(int(a), str(b)) for a, b in m.get("recommended") or []])
        return cls(region=d.get("region", "any"), gpus=[Gpu.from_api(g) for g in d.get("gpus") or []], apps=apps,
                   raw=d)


@dataclass
class Project:
    """Saved storage: restored into ~/work on GPUs launched with it, and saved back automatically."""

    id: str
    name: str
    bytes_used: int
    files: int
    created_at: datetime | None
    last_used_at: datetime | None
    in_use_by: str | None       # the machine using it right now, if any
    raw: dict = field(repr=False)

    @classmethod
    def from_api(cls, d: dict) -> Project:
        return cls(id=d["id"], name=d["name"], bytes_used=d.get("bytes_used", 0), files=d.get("files", 0),
                   created_at=_time(d.get("created_at")), last_used_at=_time(d.get("last_used_at")),
                   in_use_by=d.get("in_use_by"), raw=d)


@dataclass
class SshKey:
    id: str
    name: str
    fingerprint: str
    raw: dict = field(repr=False)

    @classmethod
    def from_api(cls, d: dict) -> SshKey:
        return cls(id=str(d["id"]), name=d["name"], fingerprint=d.get("fingerprint", ""), raw=d)


@dataclass
class LedgerEntry:
    created_at: datetime | None
    kind: str                   # topup | charge | refund | adjustment
    amount_cents: int           # + credit, − debit
    description: str
    instance: str | None
    raw: dict = field(repr=False)

    @classmethod
    def from_api(cls, d: dict) -> LedgerEntry:
        return cls(created_at=_time(d.get("created_at")), kind=d["kind"], amount_cents=d["amount_cents"],
                   description=d.get("description", ""), instance=d.get("instance"), raw=d)


@dataclass
class Instance:
    """A GPU machine. Methods act on it through the client that returned it."""

    id: str                     # e.g. "cnx-7f3a9c"
    state: str                  # requested | provisioning | running | stopping | terminated | failed
    gpu: str
    gpu_count: int
    app: str                    # its image family
    label: str | None
    price_per_hour_cents: int
    ssh_command: str | None     # while running, if you have an SSH key
    expires_at: datetime | None
    time_left_seconds: int | None
    charged_cents: int
    refunded_cents: int
    cost_so_far_cents: int
    end_reason: str | None
    startup_progress: str | None
    raw: dict = field(repr=False)
    _client: Client | None = field(default=None, repr=False, compare=False)

    @property
    def is_running(self) -> bool:
        return self.state == "running"

    @property
    def is_active(self) -> bool:
        """Starting or running: it may still cost money."""
        return self.state in ACTIVE

    @classmethod
    def from_api(cls, d: dict, client: Client | None = None) -> Instance:
        return cls(id=d["id"], state=d["state"], gpu=d["gpu"], gpu_count=d.get("gpu_count", 1),
                   app=d.get("image_family", ""), label=d.get("label"),
                   price_per_hour_cents=d.get("price_per_hour_cents", 0), ssh_command=d.get("ssh_command"),
                   expires_at=_time(d.get("expires_at")), time_left_seconds=d.get("time_left_seconds"),
                   charged_cents=d.get("charged_cents", 0), refunded_cents=d.get("refunded_cents", 0),
                   cost_so_far_cents=d.get("cost_so_far_cents", 0), end_reason=d.get("end_reason"),
                   startup_progress=d.get("startup_progress"), raw=d, _client=client)

    def _update(self, other: Instance) -> Instance:
        for name in self.__dataclass_fields__:
            if name != "_client":
                setattr(self, name, getattr(other, name))
        return self

    def _c(self) -> Client:
        if self._client is None:
            raise RuntimeError("this Instance isn't attached to a client")
        return self._client

    def refresh(self) -> Instance:
        """Fetch its current state (in place; returns itself)."""
        return self._update(self._c().get_instance(self.id))

    def wait_until_running(self, timeout: float = 900, poll: float = 10,
                           on_progress: Any = None) -> Instance:
        """Wait until it's running. Raises LaunchFailedError if it couldn't start (refunded in full), or
        WaitTimeoutError after `timeout` seconds (it may still start: check or stop it). `on_progress(instance)` is
        called after each check, e.g. to print `instance.startup_progress`."""
        deadline = time.monotonic() + timeout
        while True:
            self.refresh()
            if on_progress is not None:
                on_progress(self)
            if self.state == "running":
                return self
            if self.state not in STARTING:
                raise LaunchFailedError(f"{self.id} didn't start: {self.state}"
                                        + (f" ({self.end_reason})" if self.end_reason else ""))
            if time.monotonic() >= deadline:
                raise WaitTimeoutError(f"{self.id} wasn't running after {timeout:.0f} s (still {self.state})")
            time.sleep(poll)

    def extend(self, minutes: int | None = None, *, hours: float | None = None) -> Instance:
        """Add time at the price it launched with."""
        return self._update(self._c().extend_instance(self.id, minutes=minutes, hours=hours))

    def stop(self) -> Instance:
        """End it now: stop if running, cancel if still starting. Unused time is refunded."""
        return self._update(self._c().stop_instance(self.id))
