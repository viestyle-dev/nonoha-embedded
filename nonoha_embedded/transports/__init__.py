"""Transport strategy implementations for the embedded Runtime.

Only the host-agnostic `TcpServerStrategy` lives in this core package. Host-
specific transports live in their host binding, not here: the dart_bridge FFI
transport (`DartBridgeStrategy`) ships in the Flutter binding as the
``nonoha-dartbridge`` package (flutter_nonoha_embedded/python), which imports the
``dart_bridge`` FFI module only present inside the embedded app.
"""

from nonoha_embedded.transports.tcp_server_strategy import TcpServerStrategy

__all__ = ["TcpServerStrategy"]
