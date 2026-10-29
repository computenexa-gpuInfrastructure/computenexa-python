# Changelog

## 0.3.0

- Endpoints: a stable HTTPS address for your own server on a machine's port 8000, with its own key, e.g. one per
  customer you serve. `list_endpoints`, `get_endpoint`, `create_endpoint`, `get_or_create_endpoint`,
  `rename_endpoint`, `endpoint_key`, `rotate_endpoint_key`, `delete_endpoint`; `launch(..., endpoint=...)`.
- `Instance.endpoint_url` (the machine's own address for your server, with `api_key()`) and `Instance.endpoint_name`.
- `cnx endpoints` (ls, add, key, rm) and `cnx launch --endpoint NAME`.

## 0.2.0

- `cnx`, a command-line tool: whoami, gpus, apps, launch (asks before spending; `--yes` for scripts), ls, show, ssh,
  extend, stop, projects, keys, usage, receipts, schedules; `--json` everywhere.
- Schedules: `list_schedules`, `create_schedule`, `update_schedule`, `pause_schedule`, `resume_schedule`,
  `delete_schedule`, and `window("mon-fri", "08:00", "18:00")`.
- Model API: `Instance.api_base_url`, `Instance.api_key()`, `Client.model_api()` (the team's stable address).
- `Instance.app_url(app)`, `Instance.save_project()`, `Client.project_models(project, app)`.
- `Client.usage(days)` and `Client.receipts(...)`.

## 0.1.1

First release on PyPI (0.1.0 was never uploaded: PyPI refused its support link). Same code as 0.1.0.

## 0.1.0

First release: account and balance, catalog (GPUs, apps, recommended models, image contents), launch / get / list /
extend / stop, `with client.gpu(...)` that always stops the GPU, projects, SSH keys, ledger; one exception per kind
of refusal; reads retried on 429 and 5xx, launches never sent twice.
