# SerenMeninges

The connective tissue of the Seren brain — the shared core for **SerenMemory**,
**SerenLoci**, **SerenCorpusCallosum**, **SerenLodestar**, **SerenWorkbench**
and **SerenObservatory**. One installable package so the things that *must* be
identical across the family are identical by construction:

- **`resolve_token()`** — credentials resolution. Config holds a *pointer* to a
  secret (env var name, OS-keychain ref) or, as a deliberate escape hatch, an
  inline literal. Never the secret in plaintext unless you choose it.
- **`ServerConfig` / `TlsConfig`** + a lenient yaml/env loader.
- **`get_version()`** — the one version-getter.
- **`UpdateChecker`** — `get_version()`'s far half: is there a newer release of
  me? TTL-cached, opt-in, never in the request path. *(see below)*
- **`bearer_auth_middleware()`** — one constant-time bearer-auth implementation.
- **`render_shell()`** — the viewer's shared shell + design tokens (leaves keep
  their own tabs).

The governing rule: **core holds contracts and mechanisms, never anything
redesign-prone.** See [`../SPEC.md`](../SPEC.md) for the full design, the
inbound/outbound token symmetry, the version-coupling contract, and the
guardrail list of what deliberately stays in the leaf repos.

## Extras

Core stays light — `pyyaml` and `starlette`, both already present in every leaf
via FastAPI. Anything heavier is opt-in:

```bash
pip install seren-meninges              # the core
pip install 'seren-meninges[keyring]'   # OS-keychain token backend
pip install 'seren-meninges[updates]'   # update checking (httpx + packaging)
```

Headless Jetsons skip `[keyring]` and fall through to env-var tokens. A box
that never leaves the LAN has no reason to install `[updates]`.

## Update checking

`get_version()` answers *what am I running*. `UpdateChecker` answers *is there
something newer*, by asking the index the package came from.

```python
from seren_meninges.updates import UpdateChecker

checker = UpdateChecker("seren-lodestar")     # build it once, at startup
...
status = await checker.get()
status.as_dict()
# {'status': 'ok', 'distribution': 'seren-lodestar', 'installed': '1.4.2',
#  'latest': '1.5.0', 'update_available': True, 'detail': None,
#  'checked_at': 5412.9}
```

**It never raises and it never blocks the request path.** Results are cached for
`ttl_seconds` (6h default) behind a lock, so ten concurrent dashboard hits are
one index request. A wedged index is bounded by `timeout_seconds` (3s default).
This is deliberately *not* middleware.

**The status is always explicit** — one of four values, never a bare `None`:

| status | meaning |
|---|---|
| `ok` | we asked and got an answer; read `latest` and `update_available` |
| `disabled` | the operator switched it off |
| `unavailable` | the `[updates]` extra isn't installed |
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
format string taking `{distribution}`.

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
