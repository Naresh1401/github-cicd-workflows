import json
import subprocess

import onboard
import pytest
import yaml
from onboard import plan, quote, render


def jobs_for(paths, files=None):
    return plan(paths, files or {})


def test_python_project_with_root_dockerfile():
    jobs = jobs_for(
        ["requirements.txt", "app/main.py", "tests/test_main.py", "Dockerfile"]
    )
    assert jobs == {"python": [(".", "3.11")], "container": [("Dockerfile", ".")]}


def test_backend_and_frontend_folders():
    jobs = jobs_for(
        [
            "backend/requirements.txt",
            "backend/app.py",
            "backend/Dockerfile",
            "frontend/package.json",
        ],
        {
            "backend/Dockerfile": "FROM python:3.12-slim\nCOPY requirements.txt .\nCOPY . .\n"
        },
    )
    assert jobs["python"] == [("backend", "3.11")]
    assert jobs["node"] == ["frontend"]
    assert jobs["container"] == [("backend/Dockerfile", "backend")]


def test_dockerfile_that_copies_from_the_repository_root():
    paths = ["docker/Dockerfile", "src/app.py", "requirements.txt"]
    files = {
        "docker/Dockerfile": "FROM python:3.12-slim\nCOPY requirements.txt .\nCOPY src/ src/\n"
    }
    assert jobs_for(paths, files)["container"] == [("docker/Dockerfile", ".")]


def test_compose_file_sets_the_build_context():
    paths = ["web/Dockerfile", "web/package.json", "compose.yaml"]
    files = {
        "compose.yaml": "services:\n  web:\n    build:\n      context: ./web\n",
        "web/Dockerfile": "FROM node:22\n",
    }
    assert jobs_for(paths, files)["container"] == [("web/Dockerfile", "web")]


def test_python_version_from_requires_python():
    files = {"pyproject.toml": '[project]\nname = "x"\nrequires-python = ">=3.12"\n'}
    assert jobs_for(["pyproject.toml", "x/__init__.py"], files)["python"] == [
        (".", "3.12")
    ]


def test_pnpm_workspace_uses_the_root_only():
    jobs = jobs_for(
        [
            "package.json",
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            "apps/web/package.json",
        ]
    )
    assert jobs["node"] == ["."]


def test_docs_only_repository_needs_nothing():
    assert jobs_for(["README.md", "notes/plan.md", "slides.pdf"]) == {}


def test_vendored_files_are_ignored():
    assert (
        jobs_for(
            ["README.md", "node_modules/left-pad/package.json", ".venv/lib/site.py"]
        )
        == {}
    )


def test_opentofu_folders():
    jobs = jobs_for(
        ["infra/gcp/main.tf", "infra/azure/main.tf", "infra/azure/variables.tf"]
    )
    assert jobs == {"iac": ["infra/azure", "infra/gcp"]}


def test_rendered_workflow_is_valid_and_uses_v2():
    jobs = {
        "python": [("backend", "3.11"), ("agents", "3.12")],
        "node": ["frontend", "my app/web"],
        "container": [("backend/Dockerfile", "backend"), ("Dockerfile.web", ".")],
        "iac": ["infra"],
    }
    text = render(jobs, "DS-120")
    workflow = yaml.safe_load(text)
    triggers = workflow.get(
        "on", workflow.get(True)
    )  # YAML 1.1 reads a bare `on` as true
    assert triggers["push"]["branches"] == ["DS-120"]
    assert set(workflow["jobs"]) == {"python", "node", "container", "iac", "repo-lint"}
    assert all(job["uses"].endswith("@v2") for job in workflow["jobs"].values())
    assert workflow["jobs"]["node"]["strategy"]["matrix"]["directory"] == [
        "frontend",
        "my app/web",
    ]


def test_quote_leaves_simple_values_alone():
    assert quote("backend") == "backend"
    assert quote("CS breach/app") == "'CS breach/app'"
    assert quote("3.11") == "'3.11'"
    assert quote("it's") == "'it''s'"


def test_payload_is_sent_as_the_request_body(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs.get("input")))
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(onboard.subprocess, "run", fake_run)
    onboard.gh("-X", "POST", "repos/o/r/issues", payload={"title": "t"})
    onboard.gh("repos/o/r")
    (post, body), (get, no_body) = calls
    assert post[-2:] == ["--input", "-"] and json.loads(body) == {"title": "t"}
    assert "--input" not in get and no_body is None


def test_iac_directory_serialization_handles_quotes():
    directories = ['infra/a"b', "infra/it's", r"infra/a\b"]
    workflow = yaml.safe_load(render({"iac": directories}, "main"))
    assert json.loads(workflow["jobs"]["iac"]["with"]["directories"]) == directories


def test_workflow_paths_cannot_inject_yaml():
    with pytest.raises(RuntimeError, match="control characters"):
        render({"iac": ["infra/x\ninjected: true"]}, "main")
    assert yaml.safe_load("branch: " + quote("on"))["branch"] == "on"
    with pytest.raises(RuntimeError, match="GitHub expressions"):
        render({"iac": ["infra/${{ github.token }}"]}, "main")


def test_comment_is_not_a_shared_workflow_call(monkeypatch):
    monkeypatch.setattr(
        onboard, "tree", lambda *args: [".github/workflows/ci.yml", "app.py"]
    )
    monkeypatch.setattr(
        onboard,
        "read",
        lambda *args: "# github-cicd-workflows/.github/workflows/\njobs: {}",
    )
    monkeypatch.setattr(onboard, "gh_json", lambda *args: [])
    assert onboard.inspect("o", {"name": "r", "branch": "main"})[0] == "own-ci"


def test_v1_consumer_needs_security_migration(monkeypatch):
    monkeypatch.setattr(
        onboard, "tree", lambda *args: [".github/workflows/ci.yml", "app.py"]
    )
    monkeypatch.setattr(
        onboard,
        "read",
        lambda *args: (
            "jobs:\n  python:\n    uses: " + onboard.WORKFLOWS + "/python-ci.yml@v1"
        ),
    )
    monkeypatch.setattr(onboard, "gh_json", lambda *args: [])
    assert onboard.inspect("o", {"name": "r", "branch": "main"}) == (
        "missing",
        {"security-only": True},
    )


def test_retry_updates_existing_file_with_its_sha(monkeypatch):
    calls = []
    path = ".github/workflows/ci.yml"
    monkeypatch.setattr(onboard, "tree", lambda *args: [path])

    def fake_json(*args, **kwargs):
        endpoint = args[-1]
        if endpoint.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "base"}}
        if "/contents/" in endpoint:
            return {"sha": "file-sha", "content": "b2xk"}
        if endpoint.endswith("/pulls"):
            return {"html_url": "https://github.com/o/r/pull/1"}
        return []

    monkeypatch.setattr(onboard, "gh_json", fake_json)
    monkeypatch.setattr(
        onboard, "gh", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    onboard.open_pr("o", {"name": "r", "branch": "main"}, {"python": [(".", "3.11")]})
    updates = [kwargs["payload"] for args, kwargs in calls if path in args[-1]]
    assert updates[0]["sha"] == "file-sha"


def test_dry_run_never_closes_tracking_issues(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setattr(onboard, "gh_json", lambda *args: [{"number": 1}])
    monkeypatch.setattr(
        onboard, "gh", lambda *args, **kwargs: pytest.fail("unexpected write")
    )
    assert onboard.update_issue("o/r", [], [], [], []) is None


def test_public_repository_listing_does_not_need_tracker_metadata(monkeypatch):
    monkeypatch.setattr(
        onboard,
        "gh",
        lambda *args: json.dumps(
            {"name": "r", "private": False, "branch": "main", "owner": "o"}
        ),
    )
    assert onboard.list_repos("o", False)[0]["name"] == "r"


@pytest.mark.parametrize(
    "path", ["src/main.rs", "main.go", "app/Main.java", "Chart.yaml"]
)
def test_other_language_and_helm_repositories_receive_security(path):
    assert jobs_for([path]) == {"security-only": True}


def test_published_security_sha_pins_are_recognized(monkeypatch):
    sha = "a" * 40
    monkeypatch.setattr(onboard, "released_security_shas", lambda: {sha})
    assert onboard.secure_call(onboard.WORKFLOWS + "/security.yml@" + sha)
    assert not onboard.secure_call(onboard.WORKFLOWS + "/nonexistent.yml@v2")
    assert not onboard.secure_call(onboard.WORKFLOWS + "/security.yml@v1")
