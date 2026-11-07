"""nonoha-embedded: Embedded runtime modules for nonoha engine.

This core is host-agnostic and Dart-free. Host-specific transports (e.g. the
dart_bridge FFI transport) live in the host binding, not here — see the Flutter
binding's ``nonoha-dartbridge`` package for ``DartBridgeStrategy``.

Usage:
    from nonoha_embedded.runtime import Runtime, RuntimeConfig
    from nonoha_embedded.transports import TcpServerStrategy
    from nonoha_embedded.flow_registry import FlowRegistry
    from nonoha_embedded.message_protocol import MSG_TYPE_JSON

Graphs come from nonoha-engine workspaces as ``.vnf`` flow folders: pass the
bundled one via ``RuntimeConfig(vnf_path=...)`` and load further flows at
runtime with the ``load_flow`` wire message. Flows are the only unit of
graph loading.
"""
