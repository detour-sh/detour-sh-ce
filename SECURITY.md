# Security policy

## Supported versions

Security fixes are made on the `main` branch and included in the next release.
Please run the latest version.

## Scope

Detour is local first: one user, one install. How it is secured, and what is
your responsibility, is in [docs/security.md](docs/security.md). Hosting guidance is in
[docs/cloud-hosting.md](docs/cloud-hosting.md). Use of Detour is subject to the
[disclaimer](DISCLAIMER.md).

In scope: bypassing portal sign-in or MCP key checks, getting around the Host or Origin
checks, leaking the sign-in token or access keys, reading or writing data without
being signed in, and weaknesses in the container image or the release pipeline.

Out of scope: problems that need a host or network you left open (publishing ports 3787
or 3788 to the internet, a proxy that drops the `Host` header), a stolen token or key,
malware on the machine that runs Detour, and settings the documentation tells you not
to use, such as `DETOUR_MCP_AUTH_MODE=none` on a shared machine.

## Reporting a vulnerability

Please do not report security vulnerabilities through public issues,
discussions, or pull requests.

Report them privately through GitHub:
**[Report a vulnerability](https://github.com/detour-sh/detour-sh-ce/security/advisories/new)**
(the **Security** tab, then **Report a vulnerability**).

Please include:

- A description of the issue and its impact.
- Steps to reproduce, or a proof of concept.
- The affected version or commit.

## What to expect

- We aim to acknowledge reports within 3 business days.
- We'll keep you updated while we investigate and fix the issue.
- We'll coordinate a disclosure date with you and credit you in the advisory,
  unless you'd rather stay anonymous.

Please give us reasonable time to release a fix before disclosing publicly.
