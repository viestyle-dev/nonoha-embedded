# Contributing to nonoha-embedded

Thank you for considering a contribution! This document covers how to
make changes to this repository (pull requests and issues). For
development setup and usage, see the [README](README.md).

## Issues

- Bug reports and feature requests: the issue templates will guide you.
- Security issues: do **not** open a public issue — see
  [SECURITY.md](SECURITY.md).

## Contributor License Agreement (CLA)

Your first pull request triggers a bot comment. Reply with:

> I have read the CLA Document and I hereby sign the CLA

— that's it. The signature covers all your future contributions.

Why a CLA? Parts of nonoha are dual-licensed to fund development
(nonoha-embedded itself is AGPL-3.0-only + commercial). You keep your
copyright; you grant VIE a license that includes relicensing.
See [CLA.md](CLA.md) for the full text.

*Corporate contributors: if your employer requires a corporate
agreement, contact VIE before submitting.*

## Pull request flow

1. Fork the repository and create a branch.
2. Make your change and add tests.
3. Ensure `uv run pytest tests` passes (run
   `git submodule update --init --recursive` first).
4. Open a pull request (the template will guide you).

## Coding guidelines for changes

- Comments and docstrings are written in English.
- Keep the package host-agnostic and Dart-free (see
  [AGENTS.md](AGENTS.md) for the invariants).
- Bump the package version in `pyproject.toml` and `VERSION` in
  `nonoha_embedded/runtime.py` together on any content change.
- nonoha-core (a dependency) ships a pytest plugin that enforces the
  PortValue contract in your tests (opt out with `-p no:nonoha_core`).
