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


def test_password_is_not_shown_in_repr():
    assert "dnsinv-local" not in repr(Settings.from_env({}))
