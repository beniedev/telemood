# Public repository instructions

- Preserve the repository user's existing Git identity. Do not change it
  unless that user explicitly requests the change.
- Do not push, publish a release, create tags, or change repository
  visibility without explicit human authorization.
- Keep code, tests, examples, and documentation topology-neutral and
  content-free: use logical names only. Do not add private deployment names,
  local paths, endpoints, credentials, cookies, raw private messages, or
  runtime state.
- Keep transport/provider integrations in host adapters; the package owns
  public interaction contracts and callback state only.
