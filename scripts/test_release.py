import pytest

from release import next_version, parse_version, publish, required_bump


@pytest.mark.parametrize("tag", ["v2", "v2.01.0", "v2.0", "v2.0.0-beta.1", "2.0.0"])
def test_rejects_non_release_tags(tag):
    assert parse_version(tag) is None


def test_semver_transitions():
    assert parse_version("v1.5.0") == (1, 5, 0)
    assert next_version((1, 5, 9), "patch") == (1, 5, 10)
    assert next_version((1, 5, 9), "minor") == (1, 6, 0)
    assert next_version((1, 5, 9), "major") == (2, 0, 0)
    with pytest.raises(ValueError):
        next_version((1, 5, 0), "banana")


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        (["fix: pin dependency"], "patch"),
        (["feat(security): optional scanner"], "minor"),
        (["feat!: mandatory security"], "major"),
        (["fix: change\n\nBREAKING CHANGE: input removed"], "major"),
    ],
)
def test_required_bump(messages, expected):
    assert required_bump(messages) == expected


def fake_api(sha="abc", green=True):
    writes = []

    def call(path, method="GET", payload=None):
        if method != "GET":
            writes.append((path, method, payload))
            if path.endswith("generate-notes"):
                return {"body": "Changes"}
            if path.endswith("/releases"):
                return {"html_url": "https://github.com/o/r/releases/tag/v2.0.0"}
            return {}
        if path == "repos/o/r":
            return {"default_branch": "main"}
        if "/commits/" in path:
            return {"sha": sha}
        if "matching-refs" in path:
            return [
                {"ref": "refs/tags/v1.5.0", "object": {"sha": "old"}},
                {"ref": "refs/tags/v1", "object": {"sha": "old"}},
            ]
        if "/compare/" in path:
            return {"commits": [{"commit": {"message": "feat!: security baseline"}}]}
        if "/actions/runs" in path:
            return {
                "workflow_runs": [
                    {
                        "path": ".github/workflows/self-test.yml",
                        "head_sha": sha,
                        "conclusion": "success" if green else "failure",
                        "event": "push",
                    }
                ]
            }
        raise AssertionError(path)

    return call, writes


def test_dry_run_does_not_create_tags_or_releases(monkeypatch):
    api, writes = fake_api()
    monkeypatch.setattr("release.api", api)
    assert publish("o/r", "abc", "major", True) == "v2.0.0"
    assert all(path.endswith("generate-notes") for path, _, _ in writes)


def test_release_preserves_v1_and_creates_exact_tag_before_alias(monkeypatch):
    api, writes = fake_api()
    monkeypatch.setattr("release.api", api)
    publish("o/r", "abc", "major", False)
    tags = [payload for path, _, payload in writes if path.endswith("/git/refs")]
    assert tags == [
        {"ref": "refs/tags/v2.0.0", "sha": "abc"},
        {"ref": "refs/tags/v2", "sha": "abc"},
    ]


@pytest.mark.parametrize(
    ("sha", "bump", "green"),
    [("stale", "major", True), ("abc", "patch", True), ("abc", "major", False)],
)
def test_release_refuses_stale_commits_breaking_patch_and_failed_ci(monkeypatch, sha, bump, green):
    api, writes = fake_api(green=green)
    monkeypatch.setattr("release.api", api)
    with pytest.raises(ValueError):
        publish("o/r", sha, bump, False)
    assert not writes
