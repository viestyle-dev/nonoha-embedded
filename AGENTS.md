# AGENTS.md - AI Agent Guide

Agent-facing guide for **nonoha-embedded**, the host-agnostic Python runtime
that embeds nonoha's node-graph processing in an application.

Read these first:

- [README.md](README.md) — overview and usage
- [docs/architecture.md](docs/architecture.md) — class diagram, key classes, sequences, design notes
- [docs/api-specification.md](docs/api-specification.md) — wire protocol (JSON + binary payloads)

## Invariants (do not break)

- **Host-agnostic and Dart-free.** No Flutter/Dart/dart_bridge imports in this
  package. Host-specific transports live with their host binding (e.g. the
  Flutter binding ships `DartBridgeStrategy` in its own `nonoha-dartbridge`
  package). `TcpServerStrategy` here is the host-agnostic default.
- **Transports implement `TransportStrategy`** (`runtime.py`): `async
  run(on_message)` + `send_event(data)`. The Runtime never knows concrete
  transports.
- **The transport callback thread is not the event loop thread.** Anything
  arriving from a background thread must be scheduled with
  `asyncio.run_coroutine_threadsafe()`.
- **Bump the package version on any content change** — consumers install this
  package with pip, which caches locally built wheels by name+version.
- scipy stays out of this package and out of `nonoha-core`; scipy-based nodes
  belong in `sci-nodes`, which the app entry point injects through
  `node_registrars` (the runtime hardcodes no extension package names).

## Development commands

```bash
git submodule update --init --recursive   # packages/nonoha-core
uv sync
uv run pytest tests/ -q                   # unit tests (this package only)
```

## Consumers (examples, not dependencies)

- [flutter_nonoha_embedded](https://github.com/viestyle-dev/flutter_nonoha_embedded) — Flutter host binding (vendors/pins this package)
- [fl_nonoha_embedded_template](https://github.com/viestyle-dev/fl_nonoha_engine_embeded_template) — complete example app
