# Security Policy

## Reporting a Vulnerability

Please use GitHub's **Private Vulnerability Reporting**
(Security tab → "Report a vulnerability") on this repository.
Do not open public issues for security reports.

## Threat model

nonoha-embedded runs inside your application's process, so it has the
same privileges as your application.

- **Graph definitions can execute code.** nonoha-core (bundled as a
  submodule) includes script nodes that compile and execute Python code
  embedded in graph definitions. **Loading an untrusted flow
  (.vnf / graph.json) is equivalent to running untrusted Python code.**
  Only pass paths you trust to `RuntimeConfig.vnf_path` and `load_flow`.
- **Node sets are arbitrary Python code.** The functions you pass in
  `node_registrars` are imported and executed in-process. Only register
  node sets from sources you trust.
- **The TCP transport has no authentication.** `TcpServerStrategy` binds
  to 127.0.0.1:8765 by default and accepts every command in the protocol,
  including `load_flow` with an arbitrary path. Any process on the same
  machine can connect to it. Do not bind it to interfaces reachable from
  untrusted networks, and prefer an in-process transport provided by your
  host binding where other local processes are not trusted.

## Supported versions

Security fixes target the latest minor release.
