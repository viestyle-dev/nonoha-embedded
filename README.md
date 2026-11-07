# nonoha-embedded

Host-agnostic embedding runtime that runs nonoha's node-graph processing
([nonoha-core](https://github.com/viestyle-dev/nonoha-core)) as in-process
Python inside an application. It contains **no Dart code and no
dart_bridge dependency** — any host (Flutter, Unity, a native app, a plain
Python process) drives the same `Runtime` over a pluggable transport.

- **Runtime** — node/graph registration, message handling, event loop
- **TransportStrategy** — pluggable host channel; the host-agnostic
  `TcpServerStrategy` ships here as the default implementation
- **FlowRegistry** — flows (named graph collections, one per `.vnf`),
  input dispatch, output retrieval/subscription. A flow is also the
  GraphProxy resolution scope; graph names are unique per flow
- **Message protocol** — binary/JSON encoding of port values

See [docs/architecture.md](docs/architecture.md) for the class diagram,
design notes, and sequences, and
[docs/api-specification.md](docs/api-specification.md) for the wire protocol.

## Usage

```python
from nonoha_embedded.runtime import Runtime, RuntimeConfig
from nonoha_embedded.transports import TcpServerStrategy

config = RuntimeConfig(
    log_path="/path/to/nonoha_engine.log",   # optional
    vnf_path="flows/default.vnf",            # a nonoha-engine workspace flow
    # node_registrars=[sci_nodes.register_nodes],  # extension node sets
)
runtime = Runtime(config)
runtime.run(TcpServerStrategy())             # blocks; serves on 127.0.0.1:8765
```

`Runtime` registers all nonoha-core nodes (including GraphProxy). Extension
node sets such as the scipy-based
[sci-nodes](https://github.com/viestyle-dev/sci-nodes) are injected by the
app entry point via `node_registrars` — the runtime hardcodes no extension
package names.

Host bindings provide their own transport implementing `TransportStrategy`
and pass it to `run()`. For example, the Flutter binding
([flutter_nonoha_embedded](https://github.com/viestyle-dev/flutter_nonoha_embedded))
ships `DartBridgeStrategy` in its `nonoha-dartbridge` package; that transport
imports a host-injected FFI module and therefore lives with the binding, not
here.

## Development

```bash
git submodule update --init --recursive   # packages/nonoha-core, on first clone
uv sync                                   # includes the dev group (pytest)
uv run pytest -q
```

Invariants to preserve:

- **Host-agnostic and Dart-free** — no imports of host-specific modules.
- Bump the package version whenever content changes (consumers install this
  package with pip, which caches locally built wheels by name+version).

## License

nonoha-embedded is dual-licensed:

- **[AGPL-3.0-only](LICENSE)** — free for any use, including commercial,
  as long as you comply with the AGPLv3 obligations.
- **Commercial license** — for distribution without the AGPLv3
  obligations. Small projects receive an automatic free grant: those
  who build with it qualify while the end product's development cost
  is under JPY 1,000,000, and those who earn from the product itself
  while their revenue from it is under JPY 1,000,000 — see
  [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md) (English) /
  [COMMERCIAL-LICENSE_ja.md](COMMERCIAL-LICENSE_ja.md) (Japanese
  original; authoritative).

Code examples in this README, AGENTS.md, and docs/ are provided under the
MIT No Attribution License (MIT-0; see docs/LICENSE), so you can copy them
into your own work without attribution.

Commercial licensing contact: <https://www.viestyle.co.jp/contact/>

Copyright 2026 VIE, Inc.
