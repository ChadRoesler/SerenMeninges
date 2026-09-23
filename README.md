# SerenMeninges

The connective tissue of the Seren brain — the shared core every Seren
service wears, from the memory hemispheres to the control plane to the
viewers. One installable package so the things that *must* be identical
across the family are identical by construction:

- **`resolve_token()`** — credentials resolution. Config holds a *pointer* to a
  secret (env var name, OS-keychain ref) or, as a deliberate escape hatch, an
  inline literal. Never the secret in plaintext unless you choose it.
- **`ServerConfig` / `TlsConfig`** + a lenient yaml/env loader. The bind
  address defaults to **127.0.0.1** (since 2.3.0); a leaf that belongs on the
  LAN passes `default_host="0.0.0.0"` to `from_dict`, the same way it passes
  its `default_port`. An explicit `host:` in the yaml is honoured either way.
- **`get_version()`** — the one version-getter.
- **`bearer_auth_middleware()`** — one constant-time bearer-auth implementation,
  pure ASGI, covering HTTP and WebSocket scopes alike.
- **`UpdateChecker`** — is there a newer release of me? Cached, refreshed in
  the background, never a wait on the request path once warm.
- **`render_shell()`** — the viewer's shared shell + design tokens (leaves keep
  their own tabs).

The governing rule: **core holds contracts and mechanisms, never anything
redesign-prone.** Each module's docstring carries the reasoning for the piece
it owns; the package README under `SerenMeninges/` has the usage detail.

## License

GPL-3.0-or-later.
