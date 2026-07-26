# EveBench

EveBench exposes heterogeneous SQL query-equivalence checkers through a common
FastAPI contract.

## Design

- [Backend and API architecture](architecture.md)

## Development

- Python version: `3.11`
- Stack: `fullstack`
- CI: `github`

## Getting Started

1. Open the project in VS Code.
2. Reopen it in the dev container.
3. Run `uv sync`.
4. Run `uv run mkdocs serve` to preview the docs.

Start the API with `uv run python -m evebench`, then open `/docs` for its
OpenAPI interface.
