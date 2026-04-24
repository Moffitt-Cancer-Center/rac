"""Tests for rac_shim.routing.decision — pure host-to-route lookup."""

import uuid

import pytest

from rac_shim.routing.decision import AppRoute, route_for_host

PARENT = "rac.example.org"

FOO_ROUTE = AppRoute(
    slug="foo",
    app_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    upstream_host="foo.internal.example.org",
    access_mode="token_required",
)
BAR_ROUTE = AppRoute(
    slug="bar",
    app_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
    upstream_host="bar.internal.example.org",
    access_mode="public",
)

ROUTES = {"foo": FOO_ROUTE, "bar": BAR_ROUTE}


def test_known_host_returns_route() -> None:
    result = route_for_host("foo.rac.example.org", parent_domain=PARENT, routes=ROUTES)
    assert result == FOO_ROUTE


def test_unknown_slug_returns_none() -> None:
    result = route_for_host("baz.rac.example.org", parent_domain=PARENT, routes=ROUTES)
    assert result is None


def test_non_matching_host_returns_none() -> None:
    result = route_for_host("foo.other.example.org", parent_domain=PARENT, routes=ROUTES)
    assert result is None


def test_case_insensitive_slug() -> None:
    """FOO should resolve to the same route as foo."""
    result = route_for_host("FOO.rac.example.org", parent_domain=PARENT, routes=ROUTES)
    assert result == FOO_ROUTE


def test_port_stripped() -> None:
    result = route_for_host("foo.rac.example.org:8080", parent_domain=PARENT, routes=ROUTES)
    assert result == FOO_ROUTE


def test_trailing_dot_stripped() -> None:
    result = route_for_host("foo.rac.example.org.", parent_domain=PARENT, routes=ROUTES)
    assert result == FOO_ROUTE


def test_public_app_route_returned() -> None:
    result = route_for_host("bar.rac.example.org", parent_domain=PARENT, routes=ROUTES)
    assert result == BAR_ROUTE
    assert result is not None and result.access_mode == "public"


def test_empty_routes_returns_none() -> None:
    result = route_for_host("foo.rac.example.org", parent_domain=PARENT, routes={})
    assert result is None


def test_parent_domain_only_returns_none() -> None:
    result = route_for_host("rac.example.org", parent_domain=PARENT, routes=ROUTES)
    assert result is None
