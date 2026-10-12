"""Publish immutable SemVer releases, then advance the compatible major alias."""

import argparse
import json
import os
import re
import subprocess

VERSION = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


def api(path, method="GET", payload=None):
    command = ["gh", "api", "-X", method, path]
    if payload is not None:
        command += ["--input", "-"]
    result = subprocess.run(
        command,
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def parse_version(tag):
    match = VERSION.fullmatch(tag)
    return tuple(map(int, match.groups())) if match else None


def next_version(current, bump):
    major, minor, patch = current
    if bump == "major":
        return major + 1, 0, 0
    if bump == "minor":
        return major, minor + 1, 0
    if bump == "patch":
        return major, minor, patch + 1
    raise ValueError("Bump must be major, minor, or patch")


def required_bump(messages):
    if any(
        re.search(r"^[a-z]+(?:\([^)]+\))?!:", message)
        or "BREAKING CHANGE:" in message
        or "BREAKING-CHANGE:" in message
        for message in messages
    ):
        return "major"
    return (
        "minor"
        if any(re.match(r"feat(?:\([^)]+\))?:", m) for m in messages)
        else "patch"
    )


def publish(repo, sha, bump, dry_run):
    metadata = api(f"repos/{repo}")
    head = api(f"repos/{repo}/commits/{metadata['default_branch']}")["sha"]
    if sha != head:
        raise ValueError("Release must use the current default-branch commit")
    refs = api(f"repos/{repo}/git/matching-refs/tags/")
    versions = [
        (parse_version(ref["ref"].removeprefix("refs/tags/")), ref)
        for ref in refs
        if parse_version(ref["ref"].removeprefix("refs/tags/")) is not None
    ]
    current, _ = (
        max(versions, key=lambda entry: entry[0]) if versions else ((0, 0, 0), None)
    )
    previous = "v" + ".".join(map(str, current))
    messages = (
        [
            commit["commit"]["message"]
            for commit in api(f"repos/{repo}/compare/{previous}...{sha}")["commits"]
        ]
        if versions
        else ["feat!: initial release"]
    )
    if not messages:
        raise ValueError("No commits since the last exact release")
    minimum = required_bump(messages)
    ranks = {"patch": 0, "minor": 1, "major": 2}
    if ranks[bump] < ranks[minimum]:
        raise ValueError(f"Commit history requires at least a {minimum} release")
    version = next_version(current, bump)
    tag = "v" + ".".join(map(str, version))
    alias = f"v{version[0]}"
    # A successful run at the exact commit is required, not the branch's last green run.
    runs = api(f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100")[
        "workflow_runs"
    ]
    if not any(
        run["path"] == ".github/workflows/self-test.yml"
        and run["head_sha"] == sha
        and run["conclusion"] == "success"
        and run["event"] in {"push", "workflow_dispatch"}
        for run in runs
    ):
        raise ValueError(
            "The exact release commit needs a successful default-branch self-test"
        )
    notes = api(
        f"repos/{repo}/releases/generate-notes",
        "POST",
        {
            "tag_name": tag,
            "target_commitish": sha,
            **({"previous_tag_name": previous} if versions else {}),
        },
    )
    print(
        f"{'Dry run' if dry_run else 'Release'}: {previous} -> {tag}, {alias} -> {sha}"
    )
    if dry_run:
        return tag
    # Exact tags are create-only. Never force-update them, even during retries.
    api(f"repos/{repo}/git/refs", "POST", {"ref": f"refs/tags/{tag}", "sha": sha})
    release = api(
        f"repos/{repo}/releases",
        "POST",
        {
            "tag_name": tag,
            "name": tag,
            "body": notes["body"] + "\n\nValidated commit: " + sha,
            "draft": False,
            "prerelease": False,
            "make_latest": "true",
        },
    )
    if any(ref["ref"] == f"refs/tags/{alias}" for ref in refs):
        api(f"repos/{repo}/git/refs/tags/{alias}", "PATCH", {"sha": sha, "force": True})
    else:
        api(f"repos/{repo}/git/refs", "POST", {"ref": f"refs/tags/{alias}", "sha": sha})
    print(release["html_url"])
    return tag


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bump", required=True, choices=("patch", "minor", "major"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    publish(os.environ["GH_REPO"], os.environ["GITHUB_SHA"], args.bump, args.dry_run)


if __name__ == "__main__":
    main()
