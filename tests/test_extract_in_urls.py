"""Tests for extract_in_urls (single-snapshot /in/ URL extraction).

Regression: per-element get_attribute() re-resolves the locator for every
link and could time out (30s each) on heavy pages with detaching nodes,
freezing a task handler for minutes. The function now reads all hrefs in
one atomic evaluate_all() snapshot.
"""
from linkedin.browser.nav import extract_in_urls


class FakeLocator:
    def __init__(self, hrefs):
        self._hrefs = hrefs

    def evaluate_all(self, expression):
        assert isinstance(expression, str) and "getAttribute" in expression
        return self._hrefs


class FakePage:
    def __init__(self, hrefs, url="https://www.linkedin.com/search/results/people/"):
        self._hrefs = hrefs
        self.url = url

    def locator(self, selector):
        assert selector == 'a[href*="/in/"]'
        return FakeLocator(self._hrefs)


def test_extracts_deduped_in_urls():
    hrefs = [
        "/in/alice-smith-123?trk=public_profile",   # same profile as next line
        "/in/alice-smith-123?trk=another",          # deduped after query strip
        "/in/bob-jones-456",
        "https://www.linkedin.com/in/carol-doe-789/",
    ]
    urls = extract_in_urls(FakePage(hrefs))
    assert urls == [
        "https://www.linkedin.com/in/alice-smith-123",
        "https://www.linkedin.com/in/bob-jones-456",
        "https://www.linkedin.com/in/carol-doe-789/",
    ]


def test_ignores_non_profile_and_empty_hrefs():
    hrefs = [
        "/company/acme",   # not an /in/ profile
        "/in/valid-person-000",
        None,              # detached node snapshot entry
        "",
        "mailto:foo@example.com",
    ]
    urls = extract_in_urls(FakePage(hrefs))
    assert urls == ["https://www.linkedin.com/in/valid-person-000"]


def test_resolves_relative_hrefs_against_page_url():
    urls = extract_in_urls(
        FakePage(["/in/relative-user-111"], url="https://www.linkedin.com/feed/")
    )
    assert urls == ["https://www.linkedin.com/in/relative-user-111"]
