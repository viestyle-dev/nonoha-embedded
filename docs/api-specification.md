# nonoha-embedded API Specification

## Overview

Communication API between a host and the nonoha-embedded Runtime. Metadata
travels as JSON; PortValues travel as binary. This is a
**transport-independent payload specification** — the same payloads flow
through every `TransportStrategy` implementation.

## Architecture

```text
Host (any: Flutter / Unity / native / Python client)
    │
    └─── TransportStrategy implementation ─── nonoha-embedded Runtime
```

## Communication Protocol

### Message framing (transport-dependent)

Framing is the transport's responsibility and out of scope for this
specification:

- `TcpServerStrategy` (ships in this repository, `127.0.0.1:8765`):
  `[4 bytes length (big-endian)][payload]`
- Frame-preserving channels (e.g. the Flutter binding's PythonBridge):
  frames arrive whole, so there is no length prefix

### Payload structure

```text
[1 byte: msg_type][message data]
```

### Server-side response guarantee

Every JSON request (`0x00`) receives **exactly one response, in request
order** — the runtime processes messages serially on its event loop and
builds the response inside the same handling; unknown types, bad arguments
and handler exceptions all produce an error response. Events (`0x01`/`0x02`)
are fire-and-forget and uncorrelated. Clients combine this guarantee with
their own request serialization to correlate responses without request ids
(see the Flutter binding's communication design document).

### Message types

| msg_type | Name | Direction | Description |
| -------- | ---- | --------- | ----------- |
| `0x00` | JSON | both ways | JSON, UTF-8 encoded |
| `0x01` | EVENT_INPUT | Client→Server | binary input event (no response) |
| `0x02` | EVENT_OUTPUT | Server→Client | binary output event (push) |
| `0x03` | GET_OUTPUT_RESPONSE | Server→Client | response to a get_output request |

---

## JSON API (msg_type=0x00)

### ping

Connectivity check.

**Request:**

```json
{"type": "ping"}
```

**Response:**

```json
{"type": "pong"}
```

### version

Returns the version of the nonoha-embedded runtime.

**Request:**

```json
{"type": "version"}
```

**Response:**

```json
{"type": "version", "version": "1.0.0"}
```

### test_nonoha_core

Runs the nonoha-core integration self-tests.

**Request:**

```json
{"type": "test_nonoha_core"}
```

**Response:**

```json
{
  "type": "test_result",
  "status": "success",
  "tests": [
    {
      "name": "create_add_node",
      "passed": true,
      "details": {...}
    },
    ...
  ]
}
```

---

## Flow / Graph API

Graphs are organized by **flow** — a named collection of graphs loaded
together, one per `.vnf` (the flow name is the `.vnf` folder stem) — and
flows are the **only unit of graph loading**. Every graph-scoped operation
addresses a graph as `(flow_name, graph_name)`; graph names are unique
**within a flow only**, so different flows may reuse names like `main`. A
flow is also the resolution scope for GraphProxy nodes: a proxy resolves
its target within its own flow, and the target must be listed before the
referencing graph in `flow.json`.

Flow names starting with `_` are reserved for future runtime use and
rejected by `load_flow`.

### list_flows

Lists the flow topology (this is also how a host enumerates all graphs).

**Request:**

```json
{"type": "list_flows"}
```

**Response:**

```json
{
  "type": "list_flows_result",
  "flows": [
    {"name": "default", "builtin": true, "graphs": ["source", "main"]}
  ]
}
```

### load_flow

Loads a `.vnf` folder as a new flow (server-side, in `flow.json` order —
each graph is inserted and activated before the next one is built, so
GraphProxy nodes can resolve earlier siblings). On a mid-load failure the
whole flow is rolled back. Reserved (`_`-prefixed) flow names and duplicate
flow names are errors.

**Request:**

```json
{
  "type": "load_flow",
  "path": "/abs/path/to/flows/sample.vnf"
}
```

**Response:**

```json
{
  "type": "load_flow_result",
  "flow": "sample",
  "graphs": ["source", "main"]
}
```

### unload_flow

Unloads a flow as a unit, finalizing all its graphs. Builtin
(config-loaded) flows are refused. Note: a graph that a GraphProxy in
another still-loaded flow could never reference (scopes are per-flow), so
flow unloading cannot strand cross-flow proxies.

**Request:**

```json
{
  "type": "unload_flow",
  "flow_name": "sample"
}
```

**Response:**

```json
{
  "type": "unload_flow_result",
  "flow": "sample"
}
```

### list_graphs

Lists the graph names of one flow (`flow_name` is required; the response
shape never varies — use `list_flows` for a global view).

**Request:**

```json
{
  "type": "list_graphs",
  "flow_name": "sample"
}
```

**Response:**

```json
{
  "type": "list_graphs_result",
  "graphs": ["source", "main"]
}
```

### list_graph_inputs

Lists a graph's input ports.

**Request:**

```json
{
  "type": "list_graph_inputs",
  "flow_name": "sample",
  "graph_name": "sample_wave_generator"
}
```

**Response:**

```json
{
  "type": "list_graph_inputs_result",
  "inputs": [
    {
      "name": "test_bool",
      "values": [
        {"type": "Bool", "ndim": 0}
      ]
    }
  ]
}
```

### list_graph_outputs

Lists a graph's output ports.

**Request:**

```json
{
  "type": "list_graph_outputs",
  "flow_name": "sample",
  "graph_name": "sample_wave_generator"
}
```

**Response:**

```json
{
  "type": "list_graph_outputs_result",
  "outputs": [
    {
      "name": "test_f32",
      "values": [
        {"type": "Float32", "ndim": 0}
      ]
    }
  ]
}
```

### get_output

Fetches the latest values of a graph output port (binary response).

**Request (JSON):**

```json
{
  "type": "get_output",
  "flow_name": "sample",
  "graph_name": "sample_wave_generator",
  "port_name": "test_f32"
}
```

**Response — always binary (msg_type=0x03):**

```text
[1] msg_type = 0x03
[1] status (0=success, 1=error: unknown graph/port)
[...] port_values (PortValues binary format; value_count=0 when the port
      has not emitted yet)
```

This pull API is an **embedded-only extension** (the engine has no
equivalent — its editor receives values purely via push). The single
response shape keeps the client wait logic trivial: one binary waiter, no
JSON fallback.

### update_graph_properties

Updates graph-level properties (the `properties` block a graph declares in
its `graph.json`), following the core graph-property spec verbatim:

- Only the provided **declared** keys change. Unknown keys are skipped
  per-key (the runtime logs a Python-side warning); they are not an error
  and do not block the declared keys in the same request.
- A `GraphPropertiesChanged` node emits only when its selector intersects
  the updated keys, and then emits a snapshot of **all** its selected keys
  (unchanged ones carry their current values). Nodes also emit an initial
  snapshot at activation, so outputs are readable right after `load_flow`.
- Updates are runtime-only: they are not persisted, and reloading the flow
  (or restarting the app) restores the values from `graph.json`.

**Request:**

```json
{
  "type": "update_graph_properties",
  "flow_name": "sample",
  "graph_name": "sample_wave_generator",
  "properties": {"gain_enabled": false}
}
```

**Response:**

```json
{
  "type": "update_graph_properties_result"
}
```

Errors (standard error response): `properties` is not a JSON object, or the
flow/graph is unknown.

---

## Binary API

### EVENT_INPUT (msg_type=0x01)

Dispatches values to a graph input port. **Fire-and-forget: the server sends
no response.**

**Format:**

```text
[1] msg_type = 0x01
[1] flow_name_len
[K] flow_name (UTF-8)
[1] graph_name_len
[N] graph_name (UTF-8)
[1] event_type = 0x00 (INPUT)
[1] port_name_len
[M] port_name (UTF-8)
[...] port_values (PortValues binary format)
```

**Response:** none

---

### EVENT_OUTPUT (msg_type=0x02)

Output event pushed from server to client. Sent automatically whenever a
graph output port emits values.

**Format:**

```text
[1] msg_type = 0x02
[1] flow_name_len
[K] flow_name (UTF-8)
[1] graph_name_len
[N] graph_name (UTF-8)
[1] port_name_len
[M] port_name (UTF-8)
[...] port_values (PortValues binary format)
```

---

## PortValues Binary Format

Fully compatible with the nonoha-engine WebSocket binary format.

### Structure

```text
[1] value_count
For each value:
  [1] data_type
  [1] ndim (trailing dimensions count)
  [ndim * 2] shape (each dim as uint16 big-endian)
  [4] data_size (big-endian)
  [data_size] data payload (big-endian)
```

### Data Types

Host-side types shown are from an example client implementation (the Flutter
binding's `flutter_nonoha_client`).

| Value | Type name | numpy dtype | Host type (example: Dart) |
| ----- | --------- | ----------- | ------------------------- |
| 0x00 | Bool | bool | Uint8List |
| 0x01 | Int8 | >i1 | Int8List |
| 0x02 | Uint8 | >u1 | Uint8List |
| 0x03 | Int16 | >i2 | Int16List |
| 0x04 | Uint16 | >u2 | Uint16List |
| 0x05 | Int32 | >i4 | Int32List |
| 0x06 | Uint32 | >u4 | Uint32List |
| 0x07 | Int64 | >i8 | Int64List |
| 0x08 | Uint64 | >u8 | Uint64List |
| 0x09 | Float32 | >f4 | Float32List |
| 0x0A | Float64 | >f8 | Float64List |
| 0x0D | String | StringDType | `List<String>` |

### Shape interpretation

- `ndim` = number of trailing dimensions (excluding the first dimension)
- 1D array `[5]`: ndim=0, shape_bytes=`[]`
- 2D array `[2, 3]`: ndim=1, shape_bytes=`[3]`
- 3D array `[2, 3, 4]`: ndim=2, shape_bytes=`[3, 4]`

### String encoding

A list of NULL-terminated strings:

```text
"str1\0str2\0str3\0"
```

---

## Error Response

```json
{
  "type": "error",
  "message": "Error description"
}
```

**Response contract:** every JSON request is answered with either its
success payload (the `type` names the result, e.g. `load_flow_result`) or
this error shape — `type` alone discriminates the two, there is no
separate status field. `get_output` is the sole exception: it always
answers binary (0x03) with an in-band status byte.

---

## File Layout (this repository)

```text
nonoha_embedded/
├── __init__.py
├── runtime.py                     # Runtime, RuntimeConfig, TransportStrategy
├── transports/
│   └── tcp_server_strategy.py     # TcpServerStrategy (host-agnostic)
├── flow_registry.py               # FlowRegistry (flows of graphs + I/O)
├── graph_loader.py                # load_graph_from_dict / vnf / vng
├── message_protocol.py            # binary protocol (implements this spec)
└── testing.py                     # test_nonoha_core / test_scipy
```

### RuntimeConfig

```python
from nonoha_embedded.runtime import Runtime, RuntimeConfig

config = RuntimeConfig(
    log_path="/path/to/log.txt",       # optional
    vnf_path="flows/default.vnf",      # -> builtin flow named "default" (the stem)
    # node_registrars=[sci_nodes.register_nodes],  # extension node sets
)
runtime = Runtime(config)
runtime.run(transport_strategy)
```

### TransportStrategy

```python
class TransportStrategy(Protocol):
    async def run(self, on_message: Callable[[bytes], bytes | None]) -> None:
        """Run the communication loop."""
        ...

    def send_event(self, data: bytes) -> None:
        """Send an event to the client."""
        ...
```

Implementations:

- `TcpServerStrategy` (this repository: `nonoha_embedded/transports/`)
- `DartBridgeStrategy` (external: the Flutter binding's `nonoha-dartbridge` package)
