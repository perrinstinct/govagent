# govagent

Agent that enforces API design governance on OpenAPI 3.x specs: deterministic detection with
Spectral, minimal JSON Patch fixes proposed by an LLM and verified by re-linting, and a
reviewable GitHub PR at the end.

Work in progress. See [docs/SPEC.md](docs/SPEC.md) for the MVP specification.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Node.js (for Spectral).

```bash
uv sync
uv run pre-commit install
npm i -g @stoplight/spectral-cli@6.16.3   # same version as the Dockerfile
cp .env.example .env

uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run pytest                  # unit tests
uv run pytest -m integration   # needs Spectral on PATH

docker build -t govagent .
docker run --rm govagent spectral --version
```

## License

[MIT](LICENSE)
