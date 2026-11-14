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

    assert settings == Settings(neo4j_uri="bolt://neo4j:7687", neo4j_user="u", neo4j_password="p")


def test_password_is_not_shown_in_repr():
    assert "dnsinv-local" not in repr(Settings.from_env({}))
