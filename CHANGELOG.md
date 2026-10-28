# Changelog

## 0.1.0

First release: account and balance, catalog (GPUs, apps, recommended models, image contents), launch / get / list /
extend / stop, `with client.gpu(...)` that always stops the GPU, projects, SSH keys, ledger; one exception per kind
of refusal; reads retried on 429 and 5xx, launches never sent twice.
