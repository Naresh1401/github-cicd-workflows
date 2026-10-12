import copy
import datetime
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/security.yml").read_text())
VALIDATOR = (
    WORKFLOW["jobs"]["baseline"]["steps"][1]["run"]
    .split("<<'PY'\n", 1)[1]
    .rsplit("\nPY", 1)[0]
)
POLICY = json.loads((ROOT / ".github/security-controls.json").read_text())


def validate(tmp_path, policy):
    path = tmp_path / ".github"
    path.mkdir(exist_ok=True)
    (path / "security-controls.json").write_text(json.dumps(policy))
    return subprocess.run(
        [sys.executable, "-c", VALIDATOR], cwd=tmp_path, capture_output=True, text=True
    )


def test_tooling_repository_policy_passes(tmp_path):
    assert validate(tmp_path, POLICY).returncode == 0


def test_missing_policy_fails(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", VALIDATOR], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "::error::" in result.stdout


@pytest.mark.parametrize(
    "control",
    [
        {"status": "review-required"},
        {"status": "not-applicable", "reason": "none"},
        {"status": "tested", "evidence": ["missing.py"]},
        {"status": "tested", "evidence": ["../outside.py"]},
        {"status": "exception", "expires": "2020-01-01", "owner": "owner", "reason": "Temporary migration exception"},
        {"status": "exception", "expires": (datetime.date.today() + datetime.timedelta(days=31)).isoformat(), "owner": "owner", "reason": "Temporary migration exception"},
    ],
)
def test_unreviewed_missing_evidence_and_expired_exceptions_fail(tmp_path, control):
    policy = copy.deepcopy(POLICY)
    policy["controls"]["csrf"] = control
    assert validate(tmp_path, policy).returncode != 0


def test_applicable_control_executes_negative_tests_and_propagates_failure(tmp_path):
    policy = copy.deepcopy(POLICY)
    (tmp_path / "negative_test.py").write_text("raise SystemExit(1)\n")
    policy["controls"]["object-authorization"] = {
        "status": "tested", "evidence": ["negative_test.py"]
    }
    policy["test_command"] = f"{sys.executable} negative_test.py"
    assert validate(tmp_path, policy).returncode != 0
    (tmp_path / "negative_test.py").write_text("raise SystemExit(0)\n")
    assert validate(tmp_path, policy).returncode == 0


def test_every_reusable_workflow_requires_baseline():
    for name in ("python-ci", "node-ci", "container", "iac", "helm", "repo-lint"):
        workflow = yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text())
        assert workflow["jobs"]["security"]["uses"] == "./.github/workflows/security.yml"
        for job, settings in workflow["jobs"].items():
            if job != "security":
                needs = settings.get("needs", [])
                assert "security" in ([needs] if isinstance(needs, str) else needs) or "validate" in needs
