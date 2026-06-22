# SerenMeninges

The connective tissue of the Seren brain — the shared core for **SerenMemory**,
**SerenLoci**, and **SerenCorpusCallosum**. One installable package so the
things that *must* be identical across the three are identical by construction:

- **`resolve_token()`** — credentials resolution. Config holds a *pointer* to a
  secret (env var name, OS-keychain ref) or, as a deliberate escape hatch, an
  inline literal. Never the secret in plaintext unless you choose it.
- **`ServerConfig` / `TlsConfig`** + a lenient yaml/env loader.
- **`get_version()`** — the one version-getter.
- **`bearer_auth_middleware()`** — one constant-time bearer-auth implementation.
- **`render_shell()`** — the viewer's shared shell + design tokens (leaves keep
  their own tabs).

The governing rule: **core holds contracts and mechanisms, never anything
redesign-prone.** See [`../SPEC.md`](../SPEC.md) for the full design, the
inbound/outbound token symmetry, the version-coupling contract, and the
guardrail list of what deliberately stays in the leaf repos.

> Skeleton status: `credentials`, `version`, and `config` are real and
> smoke-tested; `auth` and `viewer` are real-shaped starting points to test
> during the build pass.

## License

GPL-3.0-or-later.
