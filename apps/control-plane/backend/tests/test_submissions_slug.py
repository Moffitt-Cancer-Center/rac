# pattern: Functional Core
"""Tests for slug derivation.

Verifies AC2.1 (slug is derived correctly from paper title or repo name).
Property-based tests verify slug invariants (format, length, uniqueness).
"""

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rac_control_plane.services.submissions.slug import derive_slug


# Example-based tests
@pytest.mark.parametrize(
    "title,repo_url,existing,expected",
    [
        # Valid title -> use it
        ("Robust Machine Learning", "https://github.com/user/my-repo", set(), "robust-machine-learning"),
        # Title with special chars -> normalize
        ("ML: A Better Approach!", "https://github.com/user/repo", set(), "ml-a-better-approach"),
        # Title collision -> fall back to repo basename, which also exists -> needs suffix
        (
            "my-repo",
            "https://github.com/user/my-repo",
            {"my-repo"},
            "my-repo-2",  # title collides, falls back to repo, repo also exists, so append -2
        ),
        # No title -> use repo basename
        (None, "https://github.com/user/grype", set(), "grype"),
        # Repo with .git suffix -> strip it
        (None, "https://github.com/user/anchore-grype.git", set(), "anchore-grype"),
        # SSH URL
        (None, "git@github.com:user/my-repo.git", set(), "my-repo"),
        # Collision with repo name -> append -2
        (None, "https://github.com/user/app", {"app"}, "app-2"),
        # Multiple collisions
        (None, "https://github.com/user/app", {"app", "app-2", "app-3"}, "app-4"),
    ],
)
def test_derive_slug_examples(title, repo_url, existing, expected):
    """Test slug derivation with specific examples."""
    result = derive_slug(title, repo_url, existing)
    assert result == expected


def test_slug_max_length():
    """Slugs are max 40 chars."""
    long_title = "A" * 100
    result = derive_slug(long_title, "https://github.com/user/repo", set())
    assert len(result) <= 40


def test_slug_no_leading_trailing_hyphens():
    """Slugs have no leading or trailing hyphens."""
    title = "--test--"
    result = derive_slug(title, "https://github.com/user/repo", set())
    assert not result.startswith("-")
    assert not result.endswith("-")


def test_slug_lowercase_alphanumeric_and_hyphens():
    """Slugs contain only lowercase letters, digits, and hyphens."""
    title = "Test123!@#$"
    result = derive_slug(title, "https://github.com/user/repo", set())
    assert re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", result)


def test_slug_fallback_to_repo_basename():
    """When title is None, use repo basename."""
    result = derive_slug(None, "https://github.com/user/my-awesome-repo", set())
    assert result == "my-awesome-repo"


def test_slug_respects_existing():
    """Slug is not in the existing set."""
    existing = {"app", "app-2", "app-3"}
    result = derive_slug(None, "https://github.com/user/app", existing)
    assert result not in existing
    assert result.startswith("app-")


# Property-based tests
@given(
    title=st.one_of(
        st.none(),
        st.text(
            alphabet=st.characters(
                blacklist_categories=("Cc", "Cs"),
                blacklist_characters="\x00",
            ),
            min_size=1,
            max_size=100,
        ),
    ),
    repo_url=st.just("https://github.com/user/test-repo"),
    existing=st.frozensets(st.text(min_size=1, max_size=40), max_size=5),
)
def test_slug_properties(title, repo_url, existing):
    """Property test: derived slug always satisfies invariants."""
    result = derive_slug(title, repo_url, existing)

    # Property 1: Matches regex
    assert re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", result), f"Invalid format: {result}"

    # Property 2: Length <= 40
    assert len(result) <= 40, f"Too long: {result}"

    # Property 3: Unique (not in existing set)
    assert result not in existing, f"Not unique: {result}"

    # Property 4: Non-empty
    assert len(result) > 0, "Slug is empty"


@given(
    title=st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
)
def test_slug_collision_handling(title):
    """Property: when slug exists, a suffix is added to make it unique."""
    repo_url = "https://github.com/user/repo"

    # First derivation
    slug1 = derive_slug(title, repo_url, set())

    # Second derivation with the first slug as existing
    # (This simulates a collision scenario)
    slug2 = derive_slug(title, repo_url, {slug1})

    # When there's a collision with the first slug, the second should be different
    # and unique against the existing set
    assert slug1 != slug2
    assert slug2 not in {slug1}


@pytest.mark.parametrize(
    "repo_url,expected_basename",
    [
        ("https://github.com/anchore/grype", "grype"),
        ("https://github.com/anchore/grype.git", "grype"),
        ("git@github.com:anchore/grype.git", "grype"),
        ("https://github.com/user/my-awesome-repo", "my-awesome-repo"),
    ],
)
def test_repo_basename_extraction(repo_url, expected_basename):
    """Test that repo basenames are extracted correctly."""
    result = derive_slug(None, repo_url, set())
    assert result == expected_basename
