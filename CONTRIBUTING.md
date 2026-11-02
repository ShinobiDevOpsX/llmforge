# Contributing to LLMForge

Thanks for your interest! This project aims to stay small, readable and
operationally sound, so please keep changes focused.

## Development setup

```bash
python3.12 -m venv .venv            # see note below if ensurepip is missing
.venv/bin/pip install -r requirements-dev.txt
```

On Debian/Ubuntu without the `python3.12-venv` package:

```bash
python3.12 -m venv --without-pip .venv
python3.12 -m pip --python .venv/bin/python install pip
.venv/bin/pip install -r requirements-dev.txt
```

## Before opening a pull request

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest --cov                 # unit tests, no Ollama needed
```

If you changed anything that talks to the runtime, also run the integration
suite against a live stack (see [docs/deployment.md](docs/deployment.md)):

```bash
.venv/bin/pytest -m integration
```

## Guidelines

- **Tests:** new behaviour needs unit tests. Unit tests must not touch the
  network; use `FakeProvider` (`tests/conftest.py`) or `respx`.
- **Metrics:** never add labels with unbounded values (prompts, user IDs,
  raw paths, API keys).
- **Logging:** never log prompts, completions or credentials. Log sizes/counts.
- **Errors:** client-facing messages must not contain internal details.
- **Providers:** new runtimes implement `app.providers.base.LLMProvider` and
  register in `app/providers/registry.py`; endpoints should not change.
- **Dependencies:** pin exact versions in `requirements*.txt`; pin container
  images by tag and digest.
- **Commits:** small, descriptive commits; reference issues where relevant.

## Reporting security issues

Please do not open public issues for vulnerabilities - see [SECURITY.md](SECURITY.md).
