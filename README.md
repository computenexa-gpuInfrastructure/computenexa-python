# ComputeNexa Python SDK

Launch, use and stop private GPUs on [ComputeNexa](https://computenexa.com) from Python. Each GPU is reserved for
your team, runs open AI models or your own code, and is paid by the minute from your team's prepaid credits.

```bash
pip install computenexa
```

## Quick start

Create an API key in the dashboard under **API keys** ("Launch and stop" to launch), then:

```bash
export CNX_API_KEY="cnxk_..."
```

```python
from computenexa import Client

cnx = Client()                                    # reads CNX_API_KEY

# Launch an RTX 4090 with JupyterLab and PyTorch for 2 hours; it's stopped when the block ends,
# even if your code fails, and unused minutes are refunded.
with cnx.gpu("rtx-4090", app="datascience", hours=2) as machine:
    print(machine.ssh_command)                    # ssh cnx-7f3a9c@ssh.computenexa.com
    ...                                           # your work
```

## From the terminal: `cnx`

The package installs a command-line tool. Same API key (`CNX_API_KEY`):

```bash
cnx whoami                                        # who the key acts as, team, balance
cnx gpus --app chat                               # GPUs for sale now, cheapest first
cnx launch rtx-4090 --app datascience --hours 2 --project titanic --create-project
cnx ls                                            # your running machines
cnx ssh cnx-7f3a9c                                # connect
cnx extend cnx-7f3a9c --hours 1
cnx stop cnx-7f3a9c                               # unused time refunded
cnx projects | cnx keys add "Work laptop" ~/.ssh/id_ed25519.pub | cnx usage | cnx receipts | cnx schedules
```

`cnx launch` shows the cost and asks first; add `--yes` in scripts. Add `--json` to any command for
machine-readable output. `cnx --help` and `cnx <command> --help` list every option.

## Choose a GPU and an app

```python
catalog = cnx.catalog()                           # live prices and stock; catalog.apps describes each app
for gpu in catalog.available(app="chat"):         # for sale now, cheapest first
    print(gpu.slug, gpu.vram_gb, "GB", gpu.price_per_hour_cents / 100, "$/h")

chat = catalog.apps["chat"]
print(chat.recommended_model(vram_gb=48))         # our model pick for a 48 GB GPU
print(cnx.image_contents("datascience"))          # every package and version installed
```

| `app=` | On the launch page | For |
|---|---|---|
| `chat` | Private Chat | chat with open AI models about your documents, in your browser |
| `imagegen` | Image Generation (ComfyUI) | images with SDXL or FLUX |
| `modelapi` | Model API (vLLM) | an AI model behind your own API key |
| `voice` | Voice Agent | speech in, spoken AI reply out |
| `datascience` | Notebook (Jupyter) | JupyterLab, PyTorch, pandas: analysis, training, fine-tuning |
| `basic` | Basic (SSH only) | Ubuntu + CUDA for your own code |

## Keep files between sessions with projects

A project is saved storage: launched with it, a GPU starts with the project's files in `~/work` and saves
`~/work` back automatically.

```python
project = cnx.get_or_create_project("titanic-training")
machine = cnx.launch("l40s", app="datascience", hours=4, project=project, label="nightly training")
machine.wait_until_running(on_progress=lambda m: print(m.state, m.startup_progress or ""))
print(machine.ssh_command)
# ... later
machine.stop()
print("refunded", machine.refunded_cents / 100)
```

## Machines

```python
cnx.list_instances(active=True)                   # your running and starting machines
cnx.list_instances(everyone=True)                 # the whole team's (owners and admins)
machine = cnx.get_instance("cnx-7f3a9c")
machine.extend(hours=1)                           # same price it launched with
machine.stop()                                    # stops if running, cancels if still starting
```

## Schedules: GPUs that start and stop by themselves

```python
from computenexa import window

schedule = cnx.create_schedule(
    "Weekdays", project=project, app="chat", gpus=["l40s", "a40"],   # first one free is used
    timezone="America/Los_Angeles", windows=[window("mon-fri", "08:00", "18:00")],
)
print(schedule.next_window_start, schedule.estimated_cents_per_month / 100)
cnx.pause_schedule(schedule, until="2026-12-01T08:00:00-08:00")
cnx.resume_schedule(schedule)
```

## Model API machines

```python
machine = cnx.launch("l40s", app="modelapi", hours=4, model="Qwen/Qwen2.5-7B-Instruct")
machine.wait_until_running()
print(machine.api_base_url, machine.api_key())    # a standard chat-completions API on your own GPU
print(cnx.model_api().base_url)                   # your team's stable address, the same on every launch
```

Other machine helpers: `machine.app_url("chat")` (a one-time signed-in link to a browser app) and
`machine.save_project()` (save the project now).

## Usage and receipts

```python
u = cnx.usage(days=30)
print(u.sessions, u.gpu_hours, u.spend_cents / 100)
for r in cnx.receipts(days=30):
    print(r.instance, r.gpu, r.used_minutes, r.net_cents / 100, r.outcome)
```

## Account, credits and SSH keys

```python
me = cnx.account()
print(me.email, me.organization.name, me.organization.role, me.balance, me.max_concurrent_gpus)
for entry in cnx.ledger(limit=20):
    print(entry.created_at, entry.description, entry.amount_cents / 100)

cnx.add_ssh_key("Work laptop", open("/Users/me/.ssh/id_ed25519.pub").read())   # the PUBLIC key only
```

## Errors

Every refusal raises a subclass of `computenexa.ComputeNexaError` with a stable `code`, a `message` you can show
and a `request_id` to quote to support:

| Exception | When |
|---|---|
| `InsufficientCreditsError` | not enough credits for the launch or extension |
| `NoCapacityError` | that GPU is fully booked right now: retry or pick another |
| `GpuLimitReachedError` | your team (or you) already run as many GPUs as allowed |
| `ProjectInUseError` | another GPU is using the project |
| `AuthenticationError` | the key is missing, mistyped or revoked |
| `PermissionDeniedError` | a look-only key tried to launch, or a dashboard-only setting |
| `NotFoundError` | no such machine or project, or not one you can see |
| `LaunchFailedError` | the machine couldn't start (refunded in full) |
| `WaitTimeoutError` | `wait_until_running` gave up; the machine may still start |

```python
from computenexa import NoCapacityError

for gpu in ["rtx-4090", "l40s", "a40"]:
    try:
        machine = cnx.launch(gpu, app="chat", hours=1)
        break
    except NoCapacityError:
        continue
```

Reads are retried automatically when the API is busy (429) or has a server error. A launch is never retried after
an uncertain failure, so it can't start two GPUs.

## Configuration

| | |
|---|---|
| `Client(api_key=...)` or `CNX_API_KEY` | your API key |
| `Client(base_url=...)` or `CNX_API_URL` | defaults to `https://api.computenexa.com/v1` |
| `Client(timeout=30, max_retries=3)` | per request |

## Help

- Guide: <https://app.computenexa.com/help/api>
- Every operation: <https://app.computenexa.com/help/api/reference>
- Support: <support@computenexa.com>

MIT licensed. © ComputeNexa.
