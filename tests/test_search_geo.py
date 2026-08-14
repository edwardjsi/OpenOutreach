"""Tests for LinkedIn People-search geography scoping (linkedin/actions/search.py).

The campaign's ``search_geo_urn`` (comma-separated LinkedIn geo URNs) is
encoded into the People-search URL as a JSON array — India + NRI-hub geos by
default. An empty value means global results (no geo filter).
"""
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlparse

from linkedin.actions.search import _initiate_search, people_search_url

INDIA_US = "103544278,103644278"
SINGLE_ENC = "%5B%22103544278%22%5D"  # ["103544278"]
MULTI_ENC = "%5B%22103544278%22%2C%22103644278%22%5D"  # ["103544278","103644278"]


def test_no_geo_urns_means_no_geo_param():
    url = people_search_url("Tech Lead Infosys", "")
    assert "geoUrn" not in url
    assert "keywords=Tech+Lead+Infosys" in url


def test_single_geo_urn_encoded_as_json_array():
    url = people_search_url("Tech Lead Infosys", "103544278")
    assert f"geoUrn={SINGLE_ENC}" in url


def test_multi_geo_urns_encoded_as_json_array():
    url = people_search_url("Tech Lead Infosys", INDIA_US)
    assert f"geoUrn={MULTI_ENC}" in url


def test_whitespace_and_blank_entries_tolerated():
    url = people_search_url("engineer", " 103544278 ,  ,103644278 ")
    assert f"geoUrn={MULTI_ENC}" in url


def test_pagination_roundtrip_preserves_geo():
    url = people_search_url("engineer", INDIA_US)
    current = urlparse(url)
    params = parse_qs(current.query)
    params["page"] = ["2"]
    again = current._replace(query=urlencode(params, doseq=True)).geturl()
    assert "geoUrn=" in again
    assert parse_qs(urlparse(again).query)["geoUrn"] == ['["103544278","103644278"]']


class _FakePage:
    def __init__(self):
        self.goto_url = None

    def goto(self, url, **kwargs):
        self.goto_url = url
        return None


def test_initiate_search_applies_campaign_geo_urns():
    page = _FakePage()
    campaign = type("C", (), {"search_geo_urn": INDIA_US})()
    session = type("S", (), {"page": page, "campaign": campaign})()

    def _goto(session, action, **kwargs):
        action()

    with patch("linkedin.actions.search.goto_page", side_effect=_goto):
        _initiate_search(session, "Tech Lead Infosys")

    assert page.goto_url is not None
    assert f"geoUrn={MULTI_ENC}" in page.goto_url
    assert "keywords=Tech+Lead+Infosys" in page.goto_url


def test_initiate_search_without_campaign_uses_no_filter():
    page = _FakePage()
    session = type("S", (), {"page": page})()

    def _goto(session, action, **kwargs):
        action()

    with patch("linkedin.actions.search.goto_page", side_effect=_goto):
        _initiate_search(session, "Tech Lead Infosys")

    assert "geoUrn" not in page.goto_url
