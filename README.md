# GitHub CI/CD Workflows

Reusable GitHub Actions workflows that run CI for all of my repositories, public and private.
Each repository calls the workflows it needs with a few lines of YAML. The jobs run inside that
repository, show up on its pull requests and use its own secrets, so nothing has to be copied
between repositories and a fix here reaches all of them.

| Workflow | What it does |
|----------|--------------|
| [`python-ci.yml`](.github/workflows/python-ci.yml) | Installs the project, lints with ruff, runs pytest, audits dependencies with pip-audit |
| [`node-ci.yml`](.github/workflows/node-ci.yml) | Installs with npm or pnpm, runs the lint, build and test scripts, runs a dependency audit |
| [`container.yml`](.github/workflows/container.yml) | Builds a Docker image, optionally smoke-tests its health endpoint and checks it runs as non-root, scans it with Trivy |
| [`iac.yml`](.github/workflows/iac.yml) | Runs `tofu fmt` and `tofu validate` for each OpenTofu/Terraform folder, scans the repository for misconfigurations with Trivy |
| [`repo-lint.yml`](.github/workflows/repo-lint.yml) | Lints GitHub workflows (actionlint), shell scripts (shellcheck) and Dockerfiles (hadolint) |

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

- **Linting** (`lint: errors`) only flags syntax errors and undefined names. `lint: full` runs
  `ruff check` and `ruff format --check` with the project's own ruff settings.
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
| `lint` | `errors` | `errors`, `full` or `off` |
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
| `directories` | `["."]` | JSON list of OpenTofu/Terraform folders |
| `tofu-version` | `1.13.1` | OpenTofu version |
| `scan` | `report` | `report`, `enforce` or `off` (Trivy misconfiguration scan) |

## repo-lint.yml

| Input | Default | Description |
|-------|---------|-------------|
| `workflow-shellcheck` | `false` | Also shellcheck the `run:` steps inside workflows |
| `shellcheck-severity` | `error` | Lowest shellcheck severity that fails the build |
| `hadolint-threshold` | `error` | Lowest hadolint severity that fails the build |

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
git tag -f v1 && git push -f origin v1
```

## Testing

[`self-test.yml`](.github/workflows/self-test.yml) runs every workflow against the sample projects
in [`tests/fixtures`](tests/fixtures) on each push and pull request.
