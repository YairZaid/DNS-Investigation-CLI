from dnsinv.config import Settings


def test_defaults_match_docker_compose():
    settings = Settings.from_env({})

    assert settings.neo4j_uri == "bolt://localhost:7687"
    assert settings.neo4j_user == "neo4j"
    assert settings.neo4j_password == "dnsinv-local"


def test_environment_overrides_defaults():
    settings = Settings.from_env(
        {"NEO4J_URI": "bolt://neo4j:7687", "NEO4J_USER": "u", "NEO4J_PASSWORD": "p"}
    )

    assert (settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password) == (
        "bolt://neo4j:7687",
        "u",
        "p",
    )


def test_browser_url_defaults_to_the_standard_port():
    assert Settings.from_env({}).neo4j_browser_url == "http://localhost:7474"


def test_browser_url_follows_the_environment_without_a_trailing_slash():
    settings = Settings.from_env({"NEO4J_BROWSER_URL": "http://localhost:7475/"})

    assert settings.neo4j_browser_url == "http://localhost:7475"


CODESPACE = {
    "CODESPACE_NAME": "dnsinv-test-abc123",
    "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev",
}


def test_locally_there_is_no_separate_browser_bolt_address():
    # docker-compose passes the Codespaces variables through as empty strings locally.
    settings = Settings.from_env(
        {"CODESPACE_NAME": "", "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": ""}
    )

    assert settings.neo4j_browser_url == "http://localhost:7474"
    assert settings.neo4j_browser_bolt_url is None


def test_inside_a_codespace_the_links_use_the_forwarded_addresses():
    settings = Settings.from_env(CODESPACE)

    assert settings.neo4j_browser_url == "https://dnsinv-test-abc123-7474.app.github.dev"
    assert settings.neo4j_browser_bolt_url == (
        "neo4j+s://dnsinv-test-abc123-7687.app.github.dev:443"
    )


def test_codespace_name_without_the_domain_is_ignored():
    settings = Settings.from_env({"CODESPACE_NAME": "dnsinv-test-abc123"})

    assert settings.neo4j_browser_url == "http://localhost:7474"
    assert settings.neo4j_browser_bolt_url is None


def test_password_is_not_shown_in_repr():
    assert "dnsinv-local" not in repr(Settings.from_env({}))
