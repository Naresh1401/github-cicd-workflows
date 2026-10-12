# GitHub CI/CD Workflows

## v2 security and Semantic Versioning

The v2 implementation adds a mandatory security baseline to every reusable CI
workflow: full-history secret detection, OWASP/GitHub Actions static analysis,
dependency/infrastructure gates, and application-specific security evidence.
Read [SECURITY-CHECKLIST.md](SECURITY-CHECKLIST.md) for applicability, negative
tests, exceptions, existing-repository migration, and limits of automated checks.

This is a **major-version change**. Existing `@v1` consumers remain unchanged.
After v2 is released, use `@v2`, an immutable exact version such as `@v2.0.0`,
or a full release commit SHA. Consumers need `.github/security-controls.json`;
onboarding creates a review-required policy rather than guessing application safety.
Python/Node audits and container/IaC scans default to `enforce` in v2. The tables
and examples below describe the legacy v1 contract where they say `report`/`@v1`.

Release via the **SemVer release** workflow, not a force-pushed exact tag.
Choose patch/minor/major and start with a dry run. Conventional Commit history
determines the minimum bump; mandatory policy changes require major. The exact
default-branch commit must pass the self-test before publication. Exact tags are
create-only; only the corresponding major alias advances. The workflow library's
SemVer is independent of each application's version.

Reusable GitHub Actions workflows that run CI for all of my repositories, public and private.
Each repository calls the workflows it needs with a few lines of YAML. The jobs run inside that
repository, show up on its pull requests and use its own secrets, so nothing has to be copied
between repositories and a fix here reaches all of them.

| Workflow | What it does |
|----------|--------------|
| [`python-ci.yml`](.github/workflows/python-ci.yml) | Installs the project, lints with ruff, runs pytest, audits dependencies with pip-audit |
| [`node-ci.yml`](.github/workflows/node-ci.yml) | Installs with npm or pnpm, runs the lint, build and test scripts, runs a dependency audit |
| [`container.yml`](.github/workflows/container.yml) | Builds a Docker image, optionally smoke-tests its health endpoint and checks it runs as non-root, scans it with Trivy |
| [`iac.yml`](.github/workflows/iac.yml) | Runs `tofu fmt`, `tofu validate` and, where a folder has them, `tofu test` for each OpenTofu/Terraform folder, and scans the repository for misconfigurations with Trivy |
| [`repo-lint.yml`](.github/workflows/repo-lint.yml) | Lints GitHub workflows (actionlint), shell scripts (shellcheck) and Dockerfiles (hadolint) |
| [`helm.yml`](.github/workflows/helm.yml) | Lints a Helm chart, validates the rendered manifests against Kubernetes schemas, and optionally installs it on a kind cluster and checks its health endpoint |

## Using it

Add `.github/workflows/ci.yml` to a repository and keep the jobs that apply:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  python:
    uses: Naresh1401/github-cicd-workflows/.github/workflows/python-ci.yml@v1

  container:
    uses: Naresh1401/github-cicd-workflows/.github/workflows/container.yml@v1

  repo-lint:
    uses: Naresh1401/github-cicd-workflows/.github/workflows/repo-lint.yml@v1
```

Settings go under `with:`. For example, a FastAPI service that should prove its image starts and
reports the commit it was built from:

```yaml
  container:
    uses: Naresh1401/github-cicd-workflows/.github/workflows/container.yml@v1
    with:
      version-build-arg: APP_VERSION
      smoke-test: true
      port: 8000
      health-path: /health
      expect-version: true
      require-non-root: true
      scan: enforce
```

Jobs that call these workflows can use a `strategy.matrix`, for example to test several Node
versions or build several Dockerfiles.

## Defaults

The defaults are chosen so that adding the workflows to an existing repository only fails for
problems that break the code. Stricter checks are one setting away.

- **Linting** (`lint: errors`) only flags syntax errors and undefined names, and skips Jupyter notebooks. `lint: check` runs
  `ruff check` with the project's own ruff settings, and `lint: full` adds `ruff format --check`.
- **Tests** run when the project has test files. A repository without tests gets a notice instead
  of a failure.
- **Dependency audits and image scans** (`audit: report`, `scan: report`) list findings in the run
  without failing it. `enforce` fails the build on them.
- **shellcheck and hadolint** fail only on errors. `shellcheck-severity` and `hadolint-threshold`
  raise the bar.

## python-ci.yml

| Input | Default | Description |
|-------|---------|-------------|
| `python-version` | `3.11` | Python version to run on |
| `working-directory` | `.` | Folder that holds the project |
| `apt-packages` | | Extra Ubuntu packages, separated by spaces |
| `install-command` | | Replaces the automatic install of `requirements-dev.txt` or `requirements.txt` plus the package itself |
| `lint` | `errors` | `errors`, `check`, `full` or `off` |
| `env-vars` | | Environment variables for the install, setup and test steps, one `KEY=value` per line (not for secrets) |
| `setup-command` | | Runs between the install and the tests, for example to generate test data |
| `test-command` | | Replaces the automatic `pytest` run |
| `audit` | `report` | `report`, `enforce` or `off` |

## node-ci.yml

| Input | Default | Description |
|-------|---------|-------------|
| `node-version` | `22` | Node.js version to run on |
| `working-directory` | `.` | Folder that holds `package.json` |
| `install-command` | | Replaces `npm ci`, `pnpm install --frozen-lockfile` or `npm install` |
| `lint-command` | `auto` | `auto` runs the `lint` script if there is one; empty skips linting |
| `build-command` | `auto` | `auto` runs the `build` script if there is one; empty skips the build |
| `test-command` | `auto` | `auto` runs the `test` script if there is a real one; empty skips the tests |
| `audit` | `report` | `report`, `enforce` or `off` (production dependencies, high and critical) |

## container.yml

| Input | Default | Description |
|-------|---------|-------------|
| `context` | `.` | Build context |
| `dockerfile` | `Dockerfile` | Path to the Dockerfile from the repository root |
| `build-args` | | Build arguments, one `KEY=value` per line |
| `version-build-arg` | | Build argument that receives the commit SHA |
| `smoke-test` | `false` | Start the image and wait for its health endpoint |
| `port` | `8000` | Port the container listens on |
| `health-path` | `/health` | Health endpoint path |
| `expect-version` | `false` | Require the health response's `version` to equal the commit SHA |
| `container-env` | | Environment variables for the smoke test, one `KEY=value` per line |
| `require-non-root` | `false` | Fail when the image runs as root |
| `scan` | `report` | `report`, `enforce` or `off` (fixable high and critical vulnerabilities) |

## iac.yml

| Input | Default | Description |
|-------|---------|-------------|
| `directories` | `["."]` | JSON list of OpenTofu/Terraform folders. Each is format-checked and validated, and its tests (`*.tftest.hcl` in the folder or in `tests/`) run with `tofu test` |
| `tofu-version` | `1.13.1` | OpenTofu version |
| `scan` | `report` | `report`, `enforce` or `off` (Trivy misconfiguration scan) |

## helm.yml

| Input | Default | Description |
|-------|---------|-------------|
| `chart` | | Path to the chart (required) |
| `values-files` | | Extra values files, one per line; each line is checked on top of `values.yaml`, and files on the same line (separated by spaces) are checked together |
| `kubernetes-version` | `1.35.0` | Kubernetes version the rendered manifests are validated against (kubeconform) |
| `kind-test` | `false` | Install the chart on a kind cluster, wait for it, check the health endpoint and run `helm test` |
| `image-dockerfile` | | Dockerfile of the image to build and load into kind; empty keeps the chart's image |
| `image-context` | `.` | Build context for that image |
| `version-build-arg` | | Build argument that receives the commit SHA |
| `install-values-files` | | Values files for the kind install, one per line, relative to the chart |
| `install-values` | | Extra `--set` settings for the kind install, one `key=value` per line |
| `setup-manifests` | | Manifests (file or folder) applied to kind before the install, such as a database the chart needs; the install waits until their workloads are ready |
| `health-path` | `/health` | Health endpoint behind the chart's Service |
| `expect-version` | `false` | Require the health response's `version` to equal the commit SHA |

For the kind test the chart should take its image from `image.repository`, `image.tag` and
`image.pullPolicy`, and label its Service with `app.kubernetes.io/instance`, as `helm create` does.

## repo-lint.yml

| Input | Default | Description |
|-------|---------|-------------|
| `workflow-shellcheck` | `false` | Also shellcheck the `run:` steps inside workflows |
| `shellcheck-severity` | `error` | Lowest shellcheck severity that fails the build |
| `hadolint-threshold` | `error` | Lowest hadolint severity that fails the build |

## Checking every repository

[`check-repos.yml`](.github/workflows/check-repos.yml) runs every morning and looks for
repositories that don't call these workflows yet:

- **Repositories with code and no CI** get a pull request that adds `.github/workflows/ci.yml`,
  built from what the repository contains (Python, Node, Dockerfiles, OpenTofu).
- **Repositories with their own `ci.yml`** are listed for review and never changed.
- **Documentation-only repositories** are skipped.

The v2 onboarding implementation also identifies actual v1 consumers and proposes
a separate security workflow and review-required controls without replacing their
existing CI. Automatic PR creation waits for the `v2` release tag to exist.
Private repositories are omitted when the tracking host is public; use a private
host for private onboarding so metadata is not exposed in public issues/logs.

Everything it finds goes into one tracking issue labelled `ci-onboarding`, which closes itself once
every repository is covered.

The default Actions token can read public repositories but can't open pull requests in other
repositories. To have the check open them itself, and to include private repositories, add a
fine-grained personal access token as the repository secret `ONBOARDING_TOKEN`:

- Repository access: all repositories
- Permissions: Contents, Pull requests, Workflows and Issues: read and write

Without the token, the tracking issue includes the suggested `ci.yml` for each repository instead.
The logic is in [`scripts/onboard.py`](scripts/onboard.py), with unit tests in
[`scripts/test_onboard.py`](scripts/test_onboard.py).

## Public on purpose

Private repositories can call workflows from a public repository, but public repositories can't
call workflows from a private one. Keeping this repository public is what lets every repository
use it. It holds no secrets: each calling repository keeps its own.

## Versioning

Repositories call `@v1`. Backwards-compatible changes move the `v1` tag; a breaking change gets
`v2`, so nothing changes under a repository until it opts in. Third-party actions are pinned to
commit SHAs, and Dependabot keeps them current.

To release a change after it is merged:

```bash
gh workflow run release.yml -f bump=major -f dry-run=true
```

## Testing

[`self-test.yml`](.github/workflows/self-test.yml) runs every workflow against the sample projects
in [`tests/fixtures`](tests/fixtures) on each push and pull request.
