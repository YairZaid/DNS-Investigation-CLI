"""Runs against a real Neo4j: `docker compose --profile dev run --rm test` or the CI service."""

import dataclasses
import os

import pytest

from dnsinv.config import Settings
from dnsinv.graph.connection import Neo4jAuthError, check_neo4j, wait_for_neo4j

pytestmark = pytest.mark.integration


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env(os.environ)


def test_reports_server_version(settings):
    version = check_neo4j(settings, timeout=30.0)

    assert version.startswith("Neo4j ")


def test_wrong_password_is_an_auth_error(settings):
    wrong = dataclasses.replace(settings, neo4j_password="definitely-wrong")

    with pytest.raises(Neo4jAuthError):
        wait_for_neo4j(wrong, timeout=10.0)
