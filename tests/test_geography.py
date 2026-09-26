from utils.geography import is_france_job


def test_france_target_accepts_country_and_known_french_places():
    assert is_france_job("France", "Remote")
    assert is_france_job(None, "Paris")
    assert is_france_job(None, "Lyon, Auvergne-Rhône-Alpes")
    assert is_france_job(None, "Remote - France")


def test_france_target_rejects_foreign_and_unknown_locations():
    assert not is_france_job("United States", "Paris")
    assert not is_france_job(None, "Palo Alto")
    assert not is_france_job(None, "Remote")
    assert not is_france_job(None, None)
