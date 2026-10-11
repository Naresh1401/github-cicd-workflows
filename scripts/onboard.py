#!/usr/bin/env python3
"""Find repositories that don't run the shared CI, and onboard them.

Checks every repository the owner has (public ones with the default Actions
token, private ones too when ONBOARDING_TOKEN is set) for a workflow that calls
github-cicd-workflows. A repository without one gets:

- a pull request that adds .github/workflows/ci.yml, when OPEN_PRS is "true"
  (needs ONBOARDING_TOKEN) and the repository has no ci.yml of its own;
- a line in the tracking issue in this repository otherwise.

Environment: GH_TOKEN, OWNER, THIS_REPO ("owner/name"), OPEN_PRS ("true"/"false"), and
DRY_RUN ("true" prints the issue instead of writing anything).
"""

import base64
import json
import os
import posixpath
import re
import subprocess
import sys

import yaml

WORKFLOWS = "Naresh1401/github-cicd-workflows/.github/workflows"
SHARED_MARKER = "github-cicd-workflows/.github/workflows/"
BRANCH = "ci/shared-workflows"
ISSUE_TITLE = "Repositories without the shared CI"
ISSUE_LABEL = "ci-onboarding"
DEP_FILE = re.compile(r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|setup\.py)$")
DOCKERFILE = re.compile(r"(^|/)Dockerfile(\.[\w-]+)?$")
COMPOSE = re.compile(r"(^|/)(docker-)?compose[^/]*\.ya?ml$")
VENDORED = re.compile(r"(^|/)(node_modules|venv|\.venv|site-packages|dist|build)/")


# ── GitHub ──────────────────────────────────────────────────────────────


def gh(*args, payload=None):
    # A JSON payload goes in as the request body through stdin
    command = ["gh", "api", *args, *(["--input", "-"] if payload is not None else [])]
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        input=json.dumps(payload) if payload is not None else None,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api {' '.join(args)}: {result.stderr.strip()[:200]}")
    return result.stdout


def gh_json(*args, payload=None):
    out = gh(*args, payload=payload)
    return json.loads(out) if out.strip() else None


def list_repos(owner, include_private):
    """Repositories the owner has, as dicts with name, branch and private."""
    path = (
        "/user/repos?affiliation=owner&per_page=100"
        if include_private
        else f"/users/{owner}/repos?type=owner&per_page=100"
    )
    jq = (
        ".[] | select(.fork | not) | select(.archived | not) | select(.size > 0)"
        " | {name, private, branch: .default_branch, owner: .owner.login}"
    )
    repos = [
        json.loads(line)
        for line in gh("--paginate", path, "--jq", jq).splitlines()
        if line
    ]
    return [r for r in repos if r["owner"] == owner]


def tree(owner, repo, branch):
    data = gh_json(f"repos/{owner}/{repo}/git/trees/{branch}?recursive=1")
    return [e["path"] for e in data["tree"] if e["type"] == "blob"]


def read(owner, repo, path, ref):
    data = gh_json(f"repos/{owner}/{repo}/contents/{path}?ref={ref}")
    return base64.b64decode(data["content"]).decode("utf-8", "replace")


# ── Planning (which shared jobs a repository needs) ─────────────────────


def dirname(path):
    return posixpath.dirname(path) or "."


def python_version(directory, files):
    """3.11 unless runtime.txt, .python-version or requires-python asks for newer."""
    for d in dict.fromkeys((directory, ".")):
        prefix = "" if d == "." else f"{d}/"
        for name in ("runtime.txt", ".python-version"):
            m = re.search(r"3\.(\d+)", files.get(prefix + name) or "")
            if m:
                return f"3.{m.group(1)}"
        m = re.search(
            r'requires-python\s*=\s*"[^"]*?>=\s*3\.(\d+)',
            files.get(prefix + "pyproject.toml") or "",
        )
        if m and int(m.group(1)) > 11:
            return f"3.{m.group(1)}"
    return "3.11"


def compose_contexts(files):
    """Dockerfile path -> build context, from docker compose files."""
    found = {}
    for path, text in files.items():
        if not COMPOSE.search(path) or not text:
            continue
        try:
            services = (yaml.safe_load(text) or {}).get("services") or {}
        except yaml.YAMLError:
            continue
        for service in services.values():
            build = (service or {}).get("build")
            if isinstance(build, str):
                build = {"context": build}
            if isinstance(build, dict):
                context = posixpath.normpath(
                    posixpath.join(dirname(path), build.get("context", "."))
                )
                found[
                    posixpath.normpath(
                        posixpath.join(context, build.get("dockerfile", "Dockerfile"))
                    )
                ] = context
    return found


def docker_context(dockerfile, text, paths):
    """The Dockerfile's folder, unless its COPY/ADD sources only exist from the repository root."""
    folder = dirname(dockerfile)
    if folder == ".":
        return "."

    def exists(prefix):
        return any(p == prefix or p.startswith(prefix + "/") for p in paths)

    for line in (text or "").splitlines():
        m = re.match(r"\s*(COPY|ADD)\s+(.*)", line, re.IGNORECASE)
        if not m or "--from" in m.group(2):
            continue
        parts = [p for p in m.group(2).split() if not p.startswith("--")]
        if parts and parts[0].startswith("["):
            try:
                parts = json.loads(" ".join(parts))
            except json.JSONDecodeError:
                continue
        for source in parts[:-1]:
            source = source.split("*")[0].rstrip("/")
            if source in ("", ".") or source.startswith("$"):
                continue
            if not exists(posixpath.normpath(f"{folder}/{source}")) and exists(source):
                return "."
    return folder


def plan(paths, files):
    """Jobs for a repository, from its file list and the contents of its build files."""
    code = [p for p in paths if not VENDORED.search(p)]
    jobs = {}
    dep_dirs = sorted({dirname(p) for p in code if DEP_FILE.search(p)})
    if "." in dep_dirs:
        py_dirs = ["."]
    elif dep_dirs:
        py_dirs = [
            d
            for d in dep_dirs
            if not re.search(r"(^|/)(tests?|docs|examples?)(/|$)", d)
        ] or ["."]
    elif any(p.endswith((".py", ".ipynb")) for p in code):
        py_dirs = ["."]
    else:
        py_dirs = []
    if py_dirs:
        jobs["python"] = [(d, python_version(d, files)) for d in py_dirs]

    node_dirs = sorted({dirname(p) for p in code if p.endswith("package.json")})
    if "." in node_dirs and any(
        p in ("pnpm-lock.yaml", "pnpm-workspace.yaml") for p in code
    ):
        node_dirs = ["."]
    if node_dirs:
        jobs["node"] = node_dirs

    contexts = compose_contexts(files)
    dockerfiles = sorted(p for p in code if DOCKERFILE.search(p))
    if dockerfiles:
        jobs["container"] = [
            (d, contexts.get(d) or docker_context(d, files.get(d), code))
            for d in dockerfiles
        ]

    tf_dirs = sorted(
        {dirname(p) for p in code if p.endswith(".tf") and "/.terraform/" not in p}
    )
    if tf_dirs:
        jobs["iac"] = tf_dirs
    return jobs


def quote(value):
    """A YAML scalar, quoted only when needed."""
    if re.fullmatch(r"[A-Za-z0-9_./-]+", value) and not re.fullmatch(r"[\d.]+", value):
        return value
    return "'" + value.replace("'", "''") + "'"


def render(jobs, branch):
    lines = [
        "name: CI",
        "",
        "on:",
        "  push:",
        f"    branches: [{quote(branch)}]",
        "  pull_request:",
        "  workflow_dispatch:",
        "",
        "permissions:",
        "  contents: read",
        "",
        "concurrency:",
        "  group: ci-${{ github.ref }}",
        "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
        "",
        "# Shared checks from https://github.com/Naresh1401/github-cicd-workflows",
        "jobs:",
    ]
    if "python" in jobs:
        lines += ["  python:", "    name: Python"]
        entries = jobs["python"]
        if len(entries) > 1:
            lines += [
                "    strategy:",
                "      fail-fast: false",
                "      matrix:",
                "        include:",
            ]
            for d, v in entries:
                lines += [
                    f"          - directory: {quote(d)}",
                    f"            python-version: {quote(v)}",
                ]
            lines += [
                f"    uses: {WORKFLOWS}/python-ci.yml@v1",
                "    with:",
                "      working-directory: ${{ matrix.directory }}",
                "      python-version: ${{ matrix.python-version }}",
            ]
        else:
            ((d, v),) = entries
            settings = (
                [f"      working-directory: {quote(d)}"] if d != "." else []
            ) + ([f"      python-version: {quote(v)}"] if v != "3.11" else [])
            lines += [f"    uses: {WORKFLOWS}/python-ci.yml@v1"] + (
                ["    with:"] + settings if settings else []
            )
        lines.append("")
    if "node" in jobs:
        dirs = jobs["node"]
        lines += ["  node:", "    name: Node"]
        if len(dirs) > 1:
            lines += [
                "    strategy:",
                "      fail-fast: false",
                "      matrix:",
                f"        directory: [{', '.join(quote(d) for d in dirs)}]",
                f"    uses: {WORKFLOWS}/node-ci.yml@v1",
                "    with:",
                "      working-directory: ${{ matrix.directory }}",
            ]
        else:
            lines += [f"    uses: {WORKFLOWS}/node-ci.yml@v1"]
            if dirs[0] != ".":
                lines += ["    with:", f"      working-directory: {quote(dirs[0])}"]
        lines.append("")
    if "container" in jobs:
        images = jobs["container"]
        lines += ["  container:", "    name: Docker image"]
        if images == [("Dockerfile", ".")]:
            lines += [f"    uses: {WORKFLOWS}/container.yml@v1"]
        elif len(images) == 1:
            ((dockerfile, context),) = images
            lines += [
                f"    uses: {WORKFLOWS}/container.yml@v1",
                "    with:",
                f"      dockerfile: {quote(dockerfile)}",
                f"      context: {quote(context)}",
            ]
        else:
            lines += [
                "    strategy:",
                "      fail-fast: false",
                "      matrix:",
                "        include:",
            ]
            for dockerfile, context in images:
                lines += [
                    f"          - dockerfile: {quote(dockerfile)}",
                    f"            context: {quote(context)}",
                ]
            lines += [
                f"    uses: {WORKFLOWS}/container.yml@v1",
                "    with:",
                "      dockerfile: ${{ matrix.dockerfile }}",
                "      context: ${{ matrix.context }}",
            ]
        lines.append("")
    if "iac" in jobs:
        dirs = ", ".join(f'"{d}"' for d in jobs["iac"])
        lines += [
            "  iac:",
            "    name: Infrastructure as code",
            f"    uses: {WORKFLOWS}/iac.yml@v1",
            "    with:",
            f"      directories: '[{dirs}]'",
            "",
        ]
    lines += [
        "  repo-lint:",
        "    name: Repository lint",
        f"    uses: {WORKFLOWS}/repo-lint.yml@v1",
    ]
    return "\n".join(lines) + "\n"


def describe(jobs):
    parts = []
    if "python" in jobs:
        parts.append("Python")
    if "node" in jobs:
        parts.append("Node")
    if "container" in jobs:
        parts.append("Docker image")
    if "iac" in jobs:
        parts.append("OpenTofu")
    return ", ".join(parts + ["repository lint"])


# ── Onboarding ──────────────────────────────────────────────────────────


def inspect(owner, repo):
    """(status, details) for one repository."""
    name, branch = repo["name"], repo["branch"]
    paths = tree(owner, name, branch)
    workflows = [
        p
        for p in paths
        if p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))
    ]
    if any(SHARED_MARKER in read(owner, name, p, branch) for p in workflows):
        return "covered", None
    for pr in gh_json(f"repos/{owner}/{name}/pulls?state=open&per_page=100") or []:
        if pr["head"]["ref"] == BRANCH or pr["title"].startswith(
            "Run CI with the shared workflows"
        ):
            return "pr-open", pr["html_url"]
    wanted = [
        p
        for p in paths
        if not VENDORED.search(p)
        and (
            DOCKERFILE.search(p)
            or COMPOSE.search(p)
            or posixpath.basename(p)
            in ("runtime.txt", ".python-version", "pyproject.toml")
        )
    ]
    files = {p: read(owner, name, p, branch) for p in wanted}
    jobs = plan(paths, files)
    if not jobs:
        return "no-code", None
    if ".github/workflows/ci.yml" in workflows:
        return "own-ci", jobs
    return "missing", jobs


def open_pr(owner, repo, jobs):
    """Opens the onboarding pull request; returns its URL."""
    name, base = repo["name"], repo["branch"]
    existing = gh_json(f"repos/{owner}/{name}/pulls?state=open&head={owner}:{BRANCH}")
    if existing:
        return existing[0]["html_url"]
    sha = gh_json(f"repos/{owner}/{name}/git/ref/heads/{base}")["object"]["sha"]
    try:
        gh(
            "-X",
            "POST",
            f"repos/{owner}/{name}/git/refs",
            payload={"ref": f"refs/heads/{BRANCH}", "sha": sha},
        )
    except RuntimeError as error:
        if "Reference already exists" not in str(error):
            raise
    content = render(jobs, base)
    gh(
        "-X",
        "PUT",
        f"repos/{owner}/{name}/contents/.github/workflows/ci.yml",
        payload={
            "message": f"Run CI with the shared workflows\n\nAdds .github/workflows/ci.yml, which calls the shared "
            f"{describe(jobs)} checks\nin {WORKFLOWS.split('/.github')[0]}.\n",
            "content": base64.b64encode(content.encode()).decode(),
            "branch": BRANCH,
        },
    )
    body = (
        f"Adds `.github/workflows/ci.yml`, which runs the shared checks from "
        f"[github-cicd-workflows](https://github.com/{WORKFLOWS.split('/.github')[0]}) on pushes to "
        f"`{base}` and on pull requests: {describe(jobs)}.\n\n"
        "Opened automatically by the daily repository check in github-cicd-workflows. Settings for "
        "stricter or custom checks are in its README.\n"
    )
    pr = gh_json(
        "-X",
        "POST",
        f"repos/{owner}/{name}/pulls",
        payload={
            "title": "Run CI with the shared workflows",
            "head": BRANCH,
            "base": base,
            "body": body,
        },
    )
    return pr["html_url"]


def update_issue(this_repo, missing, own_ci, pr_open, failed):
    """Keeps one tracking issue up to date; closes it when everything is covered."""
    issues = gh_json(f"repos/{this_repo}/issues?state=open&labels={ISSUE_LABEL}") or []
    if not missing and not own_ci and not pr_open and not failed:
        for issue in issues:
            gh(
                "-X",
                "PATCH",
                f"repos/{this_repo}/issues/{issue['number']}",
                payload={"state": "closed"},
            )
        return None
    lines = [
        "The daily repository check found repositories that don't call the shared CI.",
        "",
    ]
    if missing:
        lines += ["## Missing the shared CI", ""]
        for name, jobs, pr_url, branch in missing:
            if pr_url:
                lines.append(f"- **{name}**: {describe(jobs)}. Pull request: {pr_url}")
            else:
                lines += [
                    f"- **{name}**: {describe(jobs)}",
                    "  <details><summary>Suggested .github/workflows/ci.yml</summary>",
                    "",
                    "  ```yaml",
                    *["  " + ln for ln in render(jobs, branch).splitlines()],
                    "  ```",
                    "  </details>",
                ]
        lines.append("")
    if pr_open:
        lines += ["## Pull request open, waiting for review", ""]
        lines += [f"- **{name}**: {url}" for name, url in pr_open]
        lines.append("")
    if own_ci:
        lines += ["## Have their own ci.yml (review by hand)", ""]
        lines += [f"- **{name}**: would use {describe(jobs)}" for name, jobs in own_ci]
        lines.append("")
    if failed:
        lines += (
            ["## Could not be checked", ""]
            + [f"- **{name}**: {error}" for name, error in failed]
            + [""]
        )
    if missing and not any(pr for _, _, pr, _ in missing):
        lines.append(
            "To have the check open these pull requests itself, add a fine-grained personal access token "
            "as the repository secret `ONBOARDING_TOKEN` (all repositories; Contents, Pull requests and "
            "Workflows: read and write)."
        )
    body = "\n".join(lines) + "\n"
    if os.environ.get("DRY_RUN") == "true":
        print("\n--- tracking issue (dry run) ---\n" + body)
        return None
    if issues:
        gh(
            "-X",
            "PATCH",
            f"repos/{this_repo}/issues/{issues[0]['number']}",
            payload={"body": body},
        )
        return issues[0]["html_url"]
    try:
        gh(
            "-X",
            "POST",
            f"repos/{this_repo}/labels",
            payload={
                "name": ISSUE_LABEL,
                "color": "1D76DB",
                "description": "Repositories to move onto the shared CI",
            },
        )
    except RuntimeError:
        pass  # the label already exists
    issue = gh_json(
        "-X",
        "POST",
        f"repos/{this_repo}/issues",
        payload={"title": ISSUE_TITLE, "body": body, "labels": [ISSUE_LABEL]},
    )
    return issue["html_url"]


def main():
    owner, this_repo = os.environ["OWNER"], os.environ["THIS_REPO"]
    open_prs = os.environ.get("OPEN_PRS") == "true"
    repos = [
        r
        for r in list_repos(owner, include_private=open_prs)
        if f"{owner}/{r['name']}" != this_repo
    ]
    missing, own_ci, pr_open, failed, counts = [], [], [], [], {}
    for repo in repos:
        try:
            status, jobs = inspect(owner, repo)
            pr_url = None
            if status == "missing" and open_prs and os.environ.get("DRY_RUN") != "true":
                pr_url = open_pr(owner, repo, jobs)
        except RuntimeError as error:
            failed.append((repo["name"], str(error)[:160]))
            status = "failed"
        counts[status] = counts.get(status, 0) + 1
        if status == "missing":
            missing.append((repo["name"], jobs, pr_url, repo["branch"]))
        elif status == "own-ci":
            own_ci.append((repo["name"], jobs))
        elif status == "pr-open":
            pr_open.append((repo["name"], jobs))
        link = (
            pr_url
            if status == "missing" and pr_url
            else jobs
            if status == "pr-open"
            else ""
        )
        print(f"{repo['name']:45} {status}  {link or ''}".rstrip())
    issue = update_issue(this_repo, missing, own_ci, pr_open, failed)
    summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
    print(
        f"\n{len(repos)} repositories: {summary}"
        + (f"\nTracking issue: {issue}" if issue else "")
    )
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a") as out:
        out.write(f"### Repository check\n\n{len(repos)} repositories: {summary}\n")
        if issue:
            out.write(f"\nTracking issue: {issue}\n")
    return 1 if failed and not (missing or own_ci) and len(failed) == len(repos) else 0


if __name__ == "__main__":
    sys.exit(main())
