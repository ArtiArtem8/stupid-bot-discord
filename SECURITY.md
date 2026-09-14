# Security Policy

## Reporting a Vulnerability

Please do not report security vulnerabilities through public Issues.

Use GitHub's private vulnerability reporting form:
[Report a vulnerability](https://github.com/ArtiArtem8/stupid-bot-discord/security/advisories/new).

Include reproduction steps, the affected version or commit, and the expected impact. Keep ordinary non-security bugs in public Issues.

## Accepted dependency advisory

`GHSA-mrfv-m5wm-5w6w` affects PyNaCl, which is present because the
`discord.py[voice]` stack depends on it. The current supported discord.py release
constrains PyNaCl below the patched 1.6.2 release, so the patched version cannot be
installed without breaking the supported dependency graph.

The exception is temporarily accepted because this bot uses PyNaCl through the
Discord voice encryption path and does not call the affected custom Ed25519 point
validation API or pass application-controlled data to it. The suppression is
limited to this advisory in `.github/workflows/ci.yml` and
`.pre-commit-config.yaml`; new advisories remain failures.

Review this exception by 2026-12-15, or sooner when the discord.py voice
dependency changes. Remove the suppression as soon as the supported dependency
graph permits PyNaCl 1.6.2 or newer.
