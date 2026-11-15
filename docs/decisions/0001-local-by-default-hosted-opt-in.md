# 0001: Local by default, hosted mode opt-in with its own sign-in

- Status: accepted
- Date: 2026-11-14

## Context

Detour CE is built to run on your own computer. Its portal had no sign-in. The only
protection was that Compose publishes the ports on `127.0.0.1`, plus a Host-header check
that stops browsers and DNS rebinding.

That is not enough once the container is started another way. With the port published
beyond loopback (for example `docker run -p 3787:3787`), a client on the internet that
sent a forged `Host` header could export every route and create an MCP access key. The
Host check never stopped a direct client.

Two audiences want different things. Most people want a private, zero-setup local tool.
Some self-hosters want it on a server.

## Options

**a) Local only, forever.** Keep the loopback port and add a guard. No sign-in, nothing to
maintain. Self-hosters are on their own, and anything in front of the container (a load
balancer, a tunnel) makes the guard useless, because every request then looks private.

**b) Local, plus an optional sign-in for shared machines or LANs.** Real protection, but
it is the same work as hosting without the HTTPS and hostname handling that would make
hosting safe.

**c) Make it hostable.** A sign-in, an HTTPS public address and hostname handling. The
largest change, and it puts a public attack surface on a free project.

For the sign-in itself we considered a built-in owner login, delegating to an identity
provider, and trusting a signed header from a front proxy. A provider or proxy would tie
every self-hoster to one vendor. The built-in login works anywhere.

## Decision

Take (c), but keep local mode exactly as it was and make hosting opt-in.

- **Always on, local mode:** `netguard.py` answers `403` to any client on a globally
  routable address, on both ports. It catches the mistake of publishing a port. It is a
  safety net, not access control: a same-host proxy looks private. Turn it off with
  `DETOUR_ALLOW_PUBLIC_CLIENTS=true`.
- **Opt-in, hosted mode:** `DETOUR_HOSTED=true` plus `DETOUR_PUBLIC_URL=https://...`.
  - One owner, claimed with a one-time setup code printed in the log. Password stored as
    a salted scrypt hash, setup codes and session tokens stored hashed.
  - Cookie is `HttpOnly`, `SameSite=Strict` and `Secure`; HSTS is sent.
  - Every `/api` route needs a session except the auth routes. Writes must come from the
    `https` public origin.
  - Five wrong passwords lock sign-in, doubling from 30 seconds up to 15 minutes. Changing
    the password uses the same counter and signs out every other browser.
  - Recovery is on the host: `server.py setup-code` and `server.py reset-owner --yes`.
- **Fail closed:** a public URL without hosted mode, hosted mode without a URL, plain
  `http://` on a network, or MCP access keys turned off all stop startup.
- Hosted mode turns the client-address guard off, because behind a proxy it can't tell
  clients apart. The sign-in and MCP access keys are the gate there.

## Consequences

- People who run `docker compose up` see no change.
- The failed-password counter is global, since there is one owner. Someone hammering the
  page can keep the owner waiting up to 15 minutes. `reset-owner` is the way out.
- One owner only. No accounts, roles or sharing.
- Hosters supply HTTPS, a proxy that keeps the `Host` header, local disk for SQLite (not
  NFS or EFS), and a firewall. The README lists these.
- The data model gains `owner`, `owner_sessions` and `setup_codes` (schema 4).

## Revisit when

- A self-hoster needs more than one user. That is a different product shape, not a flag.
- Login attempts are abused in practice. A per-address limit would need the real client
  address from a trusted proxy header.
- Hosted mode needs to be tested against specific platforms, such as an AWS load balancer.
