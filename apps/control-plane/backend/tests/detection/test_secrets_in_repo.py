"""Tests for repo/secrets_in_repo rule.

Verifies:
- AWS Key ID pattern fires
- GitHub PAT pattern fires
- PEM private key header fires
- Azure storage key fires
- Clean file → 0 findings
- Secret value TRUNCATED to first_4_chars + '***' in finding detail
- Binary / oversized files skipped
- AC4.6: multiple secrets in one file → multiple findings
"""

from pathlib import Path
from uuid import uuid4

import pytest
from hypothesis import HealthCheck, given, settings as hyp_settings
from hypothesis import strategies as st

from rac_control_plane.detection.contracts import RepoContext, RepoFile
from rac_control_plane.detection.rules.repo.secrets_in_repo import RULE, _evaluate, _truncate_secret


def _ctx(files_content: dict[str, bytes], tmp_path: Path) -> RepoContext:
    """Build a RepoContext backed by real files in tmp_path."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    repo_files: list[RepoFile] = []
    for path, content in files_content.items():
        full_path = repo / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)
        repo_files.append(RepoFile(path=path, size_bytes=len(content)))

    return RepoContext(
        repo_root=repo,
        submission_id=uuid4(),
        dockerfile_path="Dockerfile",
        dockerfile_text="FROM python:3.12\n",
        files=tuple(repo_files),
        manifest=None,
        submission_metadata={},
    )


# ---------------------------------------------------------------------------
# Secret truncation helper
# ---------------------------------------------------------------------------

def test_truncate_secret_long() -> None:
    assert _truncate_secret("AKIAIOSFODNN7EXAMPLE") == "AKIA***"


def test_truncate_secret_short() -> None:
    assert _truncate_secret("ab") == "ab***"


def test_truncate_secret_exactly_four() -> None:
    assert _truncate_secret("AKIA") == "AKIA***"


# ---------------------------------------------------------------------------
# Positive cases: secrets detected
# ---------------------------------------------------------------------------

def test_aws_access_key_fires(tmp_path: Path) -> None:
    """AWS Access Key ID (AKIA...) in .env → finding with truncated value."""
    content = b"AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\nAWS_SECRET=secret\n"
    ctx = _ctx({"config.env": content}, tmp_path)
    findings = list(_evaluate(ctx))
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "repo/secrets_in_repo"
    assert "AKIA***" in f.detail
    # Full key must NOT appear verbatim
    assert "AKIAIOSFODNN7EXAMPLE" not in f.detail


def test_github_pat_ghp_fires(tmp_path: Path) -> None:
    """GitHub PAT (ghp_...) → finding."""
    pat = "ghp_" + "A" * 36
    content = f"GITHUB_TOKEN={pat}\n".encode()
    ctx = _ctx({"config.yaml": content}, tmp_path)
    findings = list(_evaluate(ctx))
    assert len(findings) == 1
    assert "ghp_***" in findings[0].detail
    assert pat not in findings[0].detail


def test_pem_private_key_fires(tmp_path: Path) -> None:
    """PEM private key header → finding."""
    content = b"-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQ...\n-----END RSA PRIVATE KEY-----\n"
    ctx = _ctx({"deploy.sh": content}, tmp_path)
    findings = list(_evaluate(ctx))
    assert len(findings) >= 1
    rule_ids = {f.rule_id for f in findings}
    assert "repo/secrets_in_repo" in rule_ids


def test_azure_storage_key_fires(tmp_path: Path) -> None:
    """Azure storage account key → finding."""
    # 86 base64 chars + ==
    b64_key = "A" * 86 + "=="
    content = f"ConnectionString=AccountKey={b64_key};EndpointSuffix=core.windows.net\n".encode()
    ctx = _ctx({"config.json": content}, tmp_path)
    findings = list(_evaluate(ctx))
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# Negative cases: clean files
# ---------------------------------------------------------------------------

def test_clean_python_no_finding(tmp_path: Path) -> None:
    content = b"import flask\napp = flask.Flask(__name__)\n"
    ctx = _ctx({"app.py": content}, tmp_path)
    findings = list(_evaluate(ctx))
    assert len(findings) == 0


def test_random_base64_no_finding(tmp_path: Path) -> None:
    """Random 86-char base64 string in a different context → no finding."""
    content = b"hash: somerandombytes_that_are_notanaccesskey\n"
    ctx = _ctx({"config.yaml": content}, tmp_path)
    findings = list(_evaluate(ctx))
    assert len(findings) == 0


# ---------------------------------------------------------------------------
# AC4.6: multiple secrets in one file → multiple findings
# ---------------------------------------------------------------------------

def test_multiple_secrets_ac46(tmp_path: Path) -> None:
    """Two different secrets in the same file → two findings."""
    pat1 = "ghp_" + "B" * 36
    pat2 = "ghp_" + "C" * 36
    content = f"TOKEN1={pat1}\nTOKEN2={pat2}\n".encode()
    ctx = _ctx({"secrets.yaml": content}, tmp_path)
    findings = list(_evaluate(ctx))
    assert len(findings) == 2


# ---------------------------------------------------------------------------
# Files that should be skipped
# ---------------------------------------------------------------------------

def test_oversized_file_skipped(tmp_path: Path) -> None:
    """Files > 1 MB are not scanned."""
    pat = "AKIA" + "X" * 16
    content = (f"KEY={pat}\n" + "x" * (2 * 1024 * 1024)).encode()
    # RepoFile with inflated size_bytes so the check triggers
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "big.py").write_bytes(content)
    repo_file = RepoFile(path="big.py", size_bytes=2 * 1024 * 1024)  # > 1 MB
    ctx = RepoContext(
        repo_root=repo,
        submission_id=uuid4(),
        dockerfile_path="Dockerfile",
        dockerfile_text="FROM python:3.12\n",
        files=(repo_file,),
        manifest=None,
        submission_metadata={},
    )
    findings = list(_evaluate(ctx))
    assert len(findings) == 0


def test_unknown_extension_skipped(tmp_path: Path) -> None:
    """Files with unrecognised extensions are not scanned."""
    pat = "AKIA" + "X" * 16
    content = f"KEY={pat}\n".encode()
    ctx = _ctx({"binary.dat": content}, tmp_path)
    findings = list(_evaluate(ctx))
    assert len(findings) == 0


# ---------------------------------------------------------------------------
# Property test: random alphanumeric text → 0 findings
# ---------------------------------------------------------------------------

@given(text=st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters="_-. \n/=:",
    ),
    min_size=0,
    max_size=200,
))
@hyp_settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_random_alphanumeric_no_finding(text: str, tmp_path: Path) -> None:
    """Property: random alphanumeric text does not trigger secret patterns."""
    # Exclude strings that accidentally contain our literal trigger patterns
    triggers = ["AKIA", "ghp_", "gho_", "ghs_", "ghr_", "github_pat_", "PRIVATE KEY", "AccountKey="]
    for t in triggers:
        if t in text:
            return  # not a useful counterexample

    content = text.encode("utf-8", errors="replace")
    ctx = RepoContext(
        repo_root=tmp_path,
        submission_id=uuid4(),
        dockerfile_path="Dockerfile",
        dockerfile_text="FROM python:3.12\n",
        files=(RepoFile(path="test.py", size_bytes=len(content)),),
        manifest=None,
        submission_metadata={},
    )
    # Write the file so ctx.read() works
    (tmp_path / "test.py").write_bytes(content)
    findings = list(_evaluate(ctx))
    assert len(findings) == 0


def test_rule_constant() -> None:
    assert RULE.rule_id == "repo/secrets_in_repo"
    assert RULE.version == 1
