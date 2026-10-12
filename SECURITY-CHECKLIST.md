# Security baseline and application checklist (v2)

Every reusable CI workflow invokes the mandatory security baseline before building,
testing, or deploying artifacts. It runs on PRs and pushes when the caller does;
onboarding also adds a weekly scheduled security workflow. Existing repositories
need a version-update PR or a separate `security.yml@v2` caller: merging here does
not change repositories pinned to v1 or to older SHAs.

## Mandatory automated gates

| Gate | Enforcement |
|---|---|
| Secrets | Gitleaks scans complete reachable Git history, including local secrets, API tokens and private keys. Findings are redacted. Rotate leaked credentials; deleting the latest copy does not resolve a historical leak. |
| Static analysis | Semgrep OSS runs OWASP Top Ten, secrets and GitHub Actions rules locally. ERROR findings block. Lower-severity application risks still require review/tests. Metrics and version checks are disabled; source is not uploaded to a scanning service. Public rule packs are downloaded and can evolve. |
| Dependencies/configuration | Trivy blocks HIGH/CRITICAL dependencies and infrastructure misconfigurations, including findings without a fix. Scanner/network errors fail rather than being interpreted as clean. |
| Runtime dependencies/images | Python and Node dependency audits, container scans and IaC scans default to enforce in v2. Their legacy report/off inputs do not disable the mandatory baseline. |
| Security evidence | Every control below has a reviewed applicability decision. Applicable controls require existing negative-test files and a successful test command. |
| Workflow hygiene | Minimal token permissions, no persisted checkout credentials, SHA-pinned Actions, bounded jobs, integrity-pinned onboarding dependencies/Corepack. |

Git history scanners and static analysis cannot prove that runtime controls are
correct. Transitive dependencies require supported lockfiles or the language
workflow's installed-dependency audit. Private package access and scanner language
support must be validated by the consuming repository. No audit claims complete
coverage of unsupported frameworks, dynamic behavior, or private applications.

## Application controls

Use `.github/security-controls.json`, schema version 1, with an accountable `owner`.
The IDs below are mandatory entries; the control itself is required only when
applicable to the application's design.

| ID | Applicability and expected negative tests |
|---|---|
| sql-injection | Database queries: parameterize queries; malicious input cannot alter query structure. |
| xss | HTML/browser rendering: contextual escaping, safe DOM APIs, no executable user HTML. |
| csrf | Cookie-authenticated state changes: reject missing/invalid CSRF protection; SameSite alone is not universally sufficient. |
| ssrf | Outbound URLs: deny internal/metadata networks, unsafe schemes and redirect/DNS bypasses. |
| file-uploads | Uploads: allowlist types, validate content and size, isolate storage, randomize names, prevent execution; malware scanning where needed. |
| path-traversal | File/path inputs: deny traversal, absolute paths, symlink escapes and unsafe archive extraction. |
| deserialization | Untrusted structured data: safe parsers, no arbitrary object construction or execution. |
| authentication | User login: reject invalid/expired credentials, prevent enumeration and recovery bypasses. |
| object-authorization | Resource APIs: another user's identifier never grants read/write/delete access (IDOR/BOLA). |
| server-permissions | Every privileged operation: enforce roles/scopes server-side; prevent mass assignment and privilege escalation. |
| tenant-isolation | Multitenant systems: cross-tenant reads, writes, jobs, caches and storage accesses fail. |
| rate-limiting | Public/expensive endpoints: login, reset, OTP and abuse quotas work across instances. |
| password-storage | Local passwords: established adaptive hashing, no plaintext/reversible storage, secure recovery and breached-password policy where appropriate. |
| mfa | High-risk/admin identities: enforce MFA and prevent enrollment/recovery/step-up bypasses. |
| sessions | Sessions: secure cookies, expiry, rotation, logout/revocation, fixation resistance. |
| jwt | JWTs: verify algorithm, signature, issuer, audience, expiry and key rotation; reject unsigned/wrong-audience tokens. |
| row-level-security | Database policy access: test using the real application role; service/admin roles must not accidentally bypass tenant isolation. RLS is not mandatory for every database architecture. |
| secrets | Secret consumers: use managed storage, minimal scopes, rotation, no client-side keys/log leaks; no committed `.env` credentials. |
| default-credentials | Provisioned/admin/test accounts: no deployable default or hardcoded password; fixture credentials cannot reach production. |
| cors | Cross-origin browser APIs: allowlist origins, reject credentialed wildcard/reflected origins. |
| webhook-signatures | Webhook receivers: verify signatures over raw bodies, reject stale/replayed/tampered messages. |
| source-maps | Browser releases: no unintended public source maps or embedded secrets; review intentionally published maps. |
| sensitive-data | Personal/confidential data: redact logs/errors/reports, minimize collection/retention and enforce access/encryption. |
| cryptography | Cryptographic use: standard libraries, strong randomness, suitable algorithms, authenticated encryption and key management. |
| security-headers | HTTP services: appropriate CSP, HSTS, anti-framing/content-type protections; validate deployment configuration. |
| resource-limits | Untrusted workloads: bound request bodies, timeouts, pagination, retries, decompression and computational costs. |

Example applicable control:

```json
{
  "status": "tested",
  "evidence": ["tests/security/test_object_access.py"]
}
```

Set the top-level `test_command` to install/run the application's security tests in
an isolated test environment. A failing command fails CI. Paths must exist in the
repository; declarations and test quality still require human review.

For a truly absent capability:

```json
{
  "status": "not-applicable",
  "reason": "This service accepts no uploads and implements no file-storage endpoint."
}
```

Temporary application-control exception:

```json
{
  "status": "exception",
  "owner": "security-owner",
  "reason": "Migration tracked in the security backlog; compensating control is deployed.",
  "expires": "YYYY-MM-DD"
}
```

Exceptions expire within 30 days and emit warnings. They do not bypass automated
secret/SAST/dependency gates. Blanket `.gitleaksignore`, `.semgrepignore`, inline
scanner allowances, and Trivy ignore files are not accepted by this baseline.
Review every exception and non-applicability decision; PR authors cannot
self-certify security without an independent review.

## Existing repositories and features

- Review feature changes against every applicable control, including failure paths.
- Keep security tests alongside application tests; avoid external credentials or
  real production data in CI.
- Do not run authenticated DAST against production. Run it against isolated,
  disposable environments when the application needs dynamic testing.
- Onboarding parses actual job calls, identifies v1 consumers without v2 coverage,
  and proposes a separate security workflow without overwriting their existing CI.
- New policies start `review-required`; owners must complete them before merging.
- Public tracking workflows omit private repositories to avoid publishing their
  names, code layouts or PR links. Run private onboarding from a private host.
- Repository owners must require the security status checks and independent
  reviews through rulesets/branch protection. Workflow code alone cannot prevent
  administrators from bypassing or removing checks.

## SemVer and release safety

The workflow implementation and security defaults are one versioned API.

- PATCH: compatible fixes.
- MINOR: backward-compatible optional checks/features.
- MAJOR: new mandatory checks, changed failing defaults, removed inputs.
- v2 introduces mandatory security and stronger audit defaults; v1 stays untouched.
- Exact tags (`v2.0.0`) are create-only. Major aliases (`v2`) advance only after
  release validation. Sensitive consumers should pin full commit SHAs.
- Use Conventional Commit PR titles (`fix:`, `feat:`, `feat!:`), or a
  `BREAKING CHANGE:` commit footer. Release automation refuses too-small bumps.
- Run **SemVer release** on the default branch with `dry-run=true` first, then
  publish explicitly. The exact commit must pass the complete self-test.
- Only the publishing job has write permission. PR workflows never receive
  release credentials. Concurrent releases are serialized.
- If publishing fails after tag creation, inspect/repair the release explicitly;
  never overwrite an exact tag as a retry shortcut.
- Roll back consumers to an earlier exact version/SHA; do not rewrite releases.

This repository's version is separate from application SemVer. No application
release tag is created or changed by these shared security workflows.
