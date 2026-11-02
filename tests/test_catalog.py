import pytest

from conftest import load_meta

catalog = load_meta("catalog")


@pytest.fixture(scope="module")
def data():
    return catalog.load_catalog()


def test_catalog_loads_with_niches_and_geos(data):
    assert data["niches"] and data["geos"]


def test_stop_word_matches_whole_words_only():
    assert catalog.find_stop_word("best seo agency", ["seo"]) == "seo"
    assert catalog.find_stop_word("great season sale", ["seo"]) == ""
    assert catalog.find_stop_word("seo tips", ["seo"], allow=["SEO"]) == ""


def test_has_signal_prefix_and_empty():
    assert catalog.has_signal("Car rental Dubai", ["rent"])
    assert not catalog.has_signal("Pizza place", ["rent"])
    assert catalog.has_signal("anything", [])


def test_build_plan_covers_every_niche_geo_pair(data):
    niche, geo = next(iter(data["niches"])), next(iter(data["geos"]))
    plan = catalog.build_plan(data, [niche], [geo], queries_per_pair=3)
    assert 1 <= len(plan) <= 3
    assert all(p["niche"] == niche and p["geo"] == geo for p in plan)
    assert len({(p["query"].lower(), p["country"]) for p in plan}) == len(plan)


def test_build_plan_rejects_unknown_ids(data):
    geo = next(iter(data["geos"]))
    with pytest.raises(ValueError):
        catalog.build_plan(data, ["nope"], [geo])
    with pytest.raises(ValueError):
        catalog.build_plan(data, [], [geo])
    with pytest.raises(ValueError):
        catalog.build_plan(data, [next(iter(data["niches"]))], ["nope"])


def test_custom_keywords_become_a_niche(data):
    geo = next(iter(data["geos"]))
    plan = catalog.build_plan(data, [], [geo], custom_keywords="tattoo studio")
    assert plan and plan[0]["niche"] == catalog.CUSTOM_NICHE_ID
