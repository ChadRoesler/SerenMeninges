# SerenMeninges

The connective tissue of the Seren brain — the shared core every Seren
service wears: Memory, Loci, Corpus Callosum, Lodestar, Observatory,
Workbench, Probe, Margin, Theatre and Symposium. One installable package so
the things that *must* be identical across the family are identical by
construction:

- **`resolve_token()`** — credentials resolution. Config holds a *pointer* to a
  secret (env var name, OS-keychain ref) or, as a deliberate escape hatch, an
  inline literal. Never the secret in plaintext unless you choose it.
- **`ServerConfig` / `TlsConfig`** + a lenient yaml/env loader. The bind
  address defaults to **127.0.0.1** (since 2.3.0); a leaf that belongs on the
  LAN passes `default_host="0.0.0.0"` to `from_dict`, the same way it passes
  its `default_port`. An explicit `host:` in the yaml is honoured either way.
- **`get_version()`** — the one version-getter.
- **`UpdateChecker`** — `get_version()`'s far half: is there a newer release of
  me? Cached, refreshed in the background, never a wait on the request path
  once warm. *(see below)*
- **`bearer_auth_middleware()`** — one constant-time bearer-auth implementation.
  Pure ASGI: it covers HTTP **and WebSocket** scopes, so a leaf that grows a
  socket route does not quietly grow an unauthenticated one. A 401 carries
  `WWW-Authenticate: Bearer`.
- **`render_shell()`** — the viewer's shared shell + design tokens (leaves keep
  their own tabs).

The governing rule: **core holds contracts and mechanisms, never anything
redesign-prone.** Each module's docstring carries the reasoning for the piece
it owns.

## Extras

Core is `pyyaml`, `starlette`, `httpx` and `packaging` — the first two already
present in every leaf via FastAPI, the last two because update checking is
core (see below). Two things are opt-in:

```bash
pip install seren-meninges              # the core
pip install 'seren-meninges[keyring]'   # OS-keychain token backend
pip install 'seren-meninges[corp]'      # OS trust store for outbound TLS (truststore)
```

Headless Jetsons skip `[keyring]` and fall through to env-var tokens. `[corp]`
is for the box behind an intercepting proxy; `TlsConfig.trust_system_store`
turns it on and the update check honours it. `[updates]` still installs but
is an empty alias kept so old installer scripts do not warn.

## Update checking

`get_version()` answers *what am I running*. `UpdateChecker` answers *is there
something newer*, by asking the index the package came from.

```python
from seren_meninges.updates import UpdateChecker

checker = UpdateChecker("seren-lodestar")     # build it once, at startup
...
await checker.start()                        # in the lifespan: warm before traffic
...
status = await checker.get()
status.as_dict()
# {'status': 'ok', 'distribution': 'seren-lodestar', 'installed': '1.4.2',
#  'latest': '1.5.0', 'update_available': True, 'detail': None,
#  'checked_at': 1789948800.0}                 # wall-clock epoch seconds
```

**It never raises, and once warm it never waits.** The first answer is fetched
once (bounded by `timeout_seconds`, 3s default) — call `start()` from the
lifespan so that happens before the first request rather than during it.
After that, `get()` returns the cached answer instantly for `ttl_seconds` (6h
default), and past the TTL it *still* returns the cached answer instantly and
refreshes in the background. `get(force=True)` is the operator's "check now"
and does wait. Ten concurrent hits share one index request. This is
deliberately *not* middleware.

**The status is always explicit** — one of four values, never a bare `None`:

| status | meaning |
|---|---|
| `ok` | we asked and got an answer; read `latest` and `update_available` |
| `disabled` | the operator switched it off |
| `unavailable` | the checker's deps are missing (a broken install), or the service never built one |
| `error` | we asked and it didn't work; `detail` says why |

That distinction is the whole point. *"I could not check"* and *"you are up to
date"* are different facts, and collapsing them produces the worst failure mode
there is: a green tick on a box that has no idea. Callers should render
`unavailable` and `error` as their own state, not as "fine."

**Prereleases are skipped** unless you pass `allow_prerelease=True`, and a
release whose every file is yanked is never offered. Comparison goes through
`packaging.version.Version`, never string compare — `"1.10.0" < "1.9.0"`
lexically, which would silently hide every tenth minor release.

Point `index_url` at a private index if you publish somewhere else; it's a
format string taking `{distribution}`. Update checking is on by default; every
leaf exposes `updates.enabled: false` in its yaml and `SEREN_<X>_UPDATES_ENABLED`
for a box that must not make outbound calls.

### What it deliberately won't do

It won't upgrade anything. **Self-upgrade is a lie in every language** — a
running process cannot reliably swap its own code out from under itself.
Applying an update is `pip install -U <package>` plus a restart, which belongs
to whatever supervises the service (NSSM, systemd), not to the service. This
reports; something outside applies.

It also won't check on import, won't check on every request, and won't tell you
about a version your own pins can't install — the index's newest is not
necessarily *your* newest.

## License

GPL-3.0-or-later.
