"""Tests for the dockerfile/inline_downloads detection rule.

Verifies all 6 required cases from the plan spec:
1. RUN wget https://... → 1 finding with correct line number
2. RUN apt-get install -y curl && curl http://... → 1 finding on curl subcommand
3. Clean RUN pip install flask → 0 findings
4. Two separate RUN lines with wget → 2 findings (AC4.6 repeat firing)
5. curl --data '{...}' https://... → 0 findings (not a download)
6. Property test: no trigger tokens → 0 findings
"""

from pathlib import Path
from uuid import uuid4

import pytest
from hypothesis import HealthCheck, given, settings as hyp_settings
from hypothesis import strategies as st

from rac_control_plane.detection.contracts import RepoContext, RepoFile
from rac_control_plane.detection.rules.dockerfile.inline_downloads import RULE, _evaluate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(dockerfile_text: str, tmp_path: Path) -> RepoContext:
    """Build a minimal RepoContext with the given Dockerfile text."""
    (tmp_path / "Dockerfile").write_text(dockerfile_text)
    return RepoContext(
        repo_root=tmp_path,
        submission_id=uuid4(),
        dockerfile_path="Dockerfile",
        dockerfile_text=dockerfile_text,
        files=(RepoFile(path="Dockerfile", size_bytes=len(dockerfile_text)),),
        manifest=None,
        submission_metadata={},
    )


def _findings(dockerfile_text: str, tmp_path: Path) -> list:
    ctx = _ctx(dockerfile_text, tmp_path)
    return list(_evaluate(ctx))


# ---------------------------------------------------------------------------
# Case 1: wget https://... → 1 finding with correct line number
# ---------------------------------------------------------------------------

def test_wget_https_fires_one_finding(tmp_path: Path) -> None:
    """RUN wget https://example.com/install.sh && sh install.sh → 1 finding."""
    df = "FROM python:3.12\nRUN wget https://example.com/install.sh && sh install.sh\n"
    findings = _findings(df, tmp_path)
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "dockerfile/inline_downloads"
    assert f.rule_version == 1
    assert f.severity == "warn"
    assert f.title == "Inline download in Dockerfile"
    # Line 2 (1-based): FROM is line 1, RUN is line 2
    assert f.line_ranges == ((2, 2),)
    assert f.file_path == "Dockerfile"
    assert f.suggested_action == "override"
    assert "wget" in f.detail


def test_wget_http_fires(tmp_path: Path) -> None:
    """wget with http:// (not just https://) also fires."""
    df = "FROM ubuntu:22.04\nRUN wget http://example.com/tool.tar.gz\n"
    findings = _findings(df, tmp_path)
    assert len(findings) == 1


def test_wget_no_url_no_finding(tmp_path: Path) -> None:
    """wget without a URL argument → no finding."""
    df = "FROM ubuntu:22.04\nRUN wget --help\n"
    findings = _findings(df, tmp_path)
    assert len(findings) == 0


# ---------------------------------------------------------------------------
# Case 2: curl in multi-command RUN → 1 finding on the curl subcommand
# ---------------------------------------------------------------------------

def test_curl_http_after_apt_fires_once(tmp_path: Path) -> None:
    """RUN apt-get install -y curl && curl http://corp/blob → 1 finding."""
    df = "FROM ubuntu:22.04\nRUN apt-get install -y curl && curl http://corp/blob\n"
    findings = _findings(df, tmp_path)
    assert len(findings) == 1
    assert "curl" in findings[0].detail


def test_curl_https_fires(tmp_path: Path) -> None:
    df = "FROM ubuntu:22.04\nRUN curl https://releases.example.com/v1.2.3/tool\n"
    findings = _findings(df, tmp_path)
    assert len(findings) == 1


def test_curl_capital_O_fires(tmp_path: Path) -> None:
    df = "FROM ubuntu:22.04\nRUN curl -O https://example.com/file.tar.gz\n"
    findings = _findings(df, tmp_path)
    assert len(findings) == 1


def test_curl_lowercase_o_fires(tmp_path: Path) -> None:
    df = "FROM ubuntu:22.04\nRUN curl -o /tmp/file.tar.gz https://example.com/file.tar.gz\n"
    findings = _findings(df, tmp_path)
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# Case 3: clean Dockerfile → 0 findings
# ---------------------------------------------------------------------------

def test_pip_install_no_finding(tmp_path: Path) -> None:
    """RUN pip install flask → no findings."""
    df = "FROM python:3.12\nRUN pip install flask\n"
    findings = _findings(df, tmp_path)
    assert len(findings) == 0


def test_apt_install_no_finding(tmp_path: Path) -> None:
    df = "FROM ubuntu:22.04\nRUN apt-get update && apt-get install -y python3\n"
    findings = _findings(df, tmp_path)
    assert len(findings) == 0


# ---------------------------------------------------------------------------
# Case 4: two separate RUN lines with wget → 2 findings (AC4.6)
# ---------------------------------------------------------------------------

def test_two_run_wget_fires_twice(tmp_path: Path) -> None:
    """Two separate RUN lines each containing wget → 2 findings (AC4.6 repeat firing)."""
    df = (
        "FROM ubuntu:22.04\n"
        "RUN wget https://example.com/tool1\n"
        "RUN wget https://example.com/tool2\n"
    )
    findings = _findings(df, tmp_path)
    assert len(findings) == 2
    # Different lines
    lines = {f.line_ranges[0][0] for f in findings}
    assert len(lines) == 2
    assert 2 in lines
    assert 3 in lines


# ---------------------------------------------------------------------------
# Case 5: curl --data → 0 findings (not a download)
# ---------------------------------------------------------------------------

def test_curl_data_post_no_finding(tmp_path: Path) -> None:
    """curl --data '{...}' https://api.example/post → 0 findings (POST, not download)."""
    df = "FROM ubuntu:22.04\nRUN curl --data '{\"key\": \"val\"}' https://api.example.com/post\n"
    findings = _findings(df, tmp_path)
    assert len(findings) == 0


def test_curl_d_short_flag_no_finding(tmp_path: Path) -> None:
    """curl -d is the short form of --data — also excluded."""
    df = "FROM ubuntu:22.04\nRUN curl -d 'payload' https://api.example.com/post\n"
    findings = _findings(df, tmp_path)
    assert len(findings) == 0


def test_curl_help_no_finding(tmp_path: Path) -> None:
    df = "FROM ubuntu:22.04\nRUN curl --help\n"
    findings = _findings(df, tmp_path)
    assert len(findings) == 0


# ---------------------------------------------------------------------------
# Other trigger patterns
# ---------------------------------------------------------------------------

def test_fetch_fires(tmp_path: Path) -> None:
    df = "FROM freebsd:latest\nRUN fetch https://example.com/file.tar.gz\n"
    findings = _findings(df, tmp_path)
    assert len(findings) == 1


def test_lwp_request_fires(tmp_path: Path) -> None:
    df = "FROM ubuntu:22.04\nRUN lwp-request https://example.com/resource\n"
    findings = _findings(df, tmp_path)
    assert len(findings) == 1


def test_git_clone_http_fires(tmp_path: Path) -> None:
    df = "FROM ubuntu:22.04\nRUN git clone https://github.com/owner/repo.git\n"
    findings = _findings(df, tmp_path)
    assert len(findings) == 1


def test_git_clone_ssh_no_finding(tmp_path: Path) -> None:
    """git clone with SSH URL does not fire (only http(s) is flagged)."""
    df = "FROM ubuntu:22.04\nRUN git clone git@github.com:owner/repo.git\n"
    findings = _findings(df, tmp_path)
    assert len(findings) == 0


# ---------------------------------------------------------------------------
# Line continuation handling
# ---------------------------------------------------------------------------

def test_wget_with_line_continuation(tmp_path: Path) -> None:
    """RUN with trailing backslash continuation still detected."""
    df = (
        "FROM ubuntu:22.04\n"
        "RUN apt-get update \\\n"
        "    && wget https://example.com/install.sh\n"
    )
    findings = _findings(df, tmp_path)
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# Case 6: Property test — no trigger tokens → 0 findings
# ---------------------------------------------------------------------------

_SAFE_COMMANDS = st.sampled_from([
    "pip install {pkg}",
    "apt-get install -y {pkg}",
    "npm install {pkg}",
    "yum install -y {pkg}",
    "conda install {pkg}",
    "echo hello",
    "cp /src /dst",
    "mkdir -p /app",
    "chmod +x /entrypoint.sh",
])

_SAFE_PKGS = st.from_regex(r"[a-zA-Z0-9_-]{3,20}", fullmatch=True)


@given(cmd_template=_SAFE_COMMANDS, pkg=_SAFE_PKGS)
@hyp_settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property_no_trigger_tokens_zero_findings(
    cmd_template: str,
    pkg: str,
    tmp_path: Path,
) -> None:
    """Property: Dockerfile without wget/curl/fetch/lwp-request/git clone http → 0 findings."""
    cmd = cmd_template.format(pkg=pkg)
    # Extra guard: the generated text must not accidentally contain our trigger words
    for trigger in ("wget", "curl", "fetch", "lwp-request", "git clone"):
        if trigger in cmd:
            return  # skip this example — not a useful counterexample

    df = f"FROM python:3.12\nRUN {cmd}\n"
    ctx = RepoContext(
        repo_root=tmp_path,
        submission_id=uuid4(),
        dockerfile_path="Dockerfile",
        dockerfile_text=df,
        files=(),
        manifest=None,
        submission_metadata={},
    )
    findings = list(_evaluate(ctx))
    assert len(findings) == 0, f"Unexpected findings for: RUN {cmd}"


# ---------------------------------------------------------------------------
# RULE module-level constant is correct
# ---------------------------------------------------------------------------

def test_rule_constant() -> None:
    """RULE constant has correct rule_id, version, and severity."""
    assert RULE.rule_id == "dockerfile/inline_downloads"
    assert RULE.version == 1
    assert RULE.default_severity == "warn"
    assert callable(RULE.evaluate)
