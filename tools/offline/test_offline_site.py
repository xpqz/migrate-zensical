"""Checks on a built, extracted offline bundle named by OFFLINE_SITE.

The bundle must work with no network: no page may load anything that is
not a file inside the bundle.
"""

import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from bs4 import BeautifulSoup

_SITE_ENV = os.environ.get("OFFLINE_SITE", "")
SITE = Path(_SITE_ENV)
pytestmark = pytest.mark.skipif(not _SITE_ENV, reason="OFFLINE_SITE not set")

SAMPLE_PAGES = [
    "index.html",
    "language-reference-guide/primitive-functions/union.html",
    "language-reference-guide/symbols/rho.html",
    "object-reference/objects/htmlrenderer.html",
]

RESOURCE_ATTRS = {
    "script": "src",
    "link": "href",
    "img": "src",
    "iframe": "src",
    "source": "src",
    "video": "src",
    "audio": "src",
    "embed": "src",
    "object": "data",
}


def _html_files():
    return sorted(SITE.rglob("*.html"))


def _is_local(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme in ("", "file") and not url.startswith("//"):
        return not (parts.scheme == "" and url.startswith("/"))
    return parts.scheme in ("data", "blob", "about", "javascript")


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    remote = []

    def guard(route):
        url = route.request.url
        if url.startswith(("file:", "data:", "blob:", "about:")):
            route.continue_()
        else:
            remote.append(url)
            route.abort()

    context.route("**/*", guard)
    pg = context.new_page()
    pg.remote = remote
    yield pg
    context.close()


def test_no_network_requests(page):
    for rel in SAMPLE_PAGES:
        page.goto((SITE / rel).as_uri())
        page.wait_for_load_state("load")
    assert page.remote == []


def test_search_works_offline(page):
    page.goto((SITE / "index.html").as_uri())
    page.click(".md-search__button")
    page.locator("input[type=search], input[type=text], [role=searchbox], [role=combobox]").first.fill("union")
    target = (SITE / "language-reference-guide/primitive-functions/union.html").as_uri()
    page.locator(f'a[href^="{target}"]').first.wait_for(timeout=15000)
    assert page.remote == []


def test_no_remote_resources_anywhere():
    offenders = []
    for f in _html_files():
        soup = BeautifulSoup(f.read_text(encoding="utf-8"), "html.parser")
        for tag, attr in RESOURCE_ATTRS.items():
            for el in soup.find_all(tag):
                url = el.get(attr)
                if url and not _is_local(url):
                    offenders.append(f"{f.relative_to(SITE)}: <{tag} {attr}={url}>")
        if soup.find("link", rel="canonical"):
            offenders.append(f"{f.relative_to(SITE)}: canonical link")
    assert offenders == []


def _known_broken() -> set[str]:
    lines = (Path(__file__).parent / "known-broken-links.txt").read_text(encoding="utf-8").splitlines()
    return {ln for ln in lines if ln.strip() and not ln.startswith("#")}


def test_relative_links_resolve():
    broken = set()
    for f in _html_files():
        soup = BeautifulSoup(f.read_text(encoding="utf-8"), "html.parser")
        urls = [a.get("href") for a in soup.find_all("a")]
        urls += [el.get(attr) for tag, attr in RESOURCE_ATTRS.items() for el in soup.find_all(tag)]
        for url in urls:
            if not url or urlsplit(url).scheme or url.startswith(("#", "//")):
                continue
            path = unquote(urlsplit(url).path)
            if not path:
                continue
            target = (SITE / path.lstrip("/")) if path.startswith("/") else (f.parent / path)
            if not target.resolve().exists():
                broken.add(f"{f.relative_to(SITE).as_posix()}: {url}")
    known = _known_broken()
    assert sorted(broken - known) == [], "new broken links"
    assert sorted(known - broken) == [], "fixed links still listed in known-broken-links.txt"
    assert not (SITE / "404.html").exists()


@pytest.mark.xfail(strict=True, reason="same-version absolute links upstream; plan Task 6")
def test_no_same_version_absolute_links():
    version = (SITE.name.removeprefix("dyalog-docs-")) if SITE.name.startswith("dyalog-docs-") else "21.0"
    prefix = f"https://docs.dyalog.com/{version}/"
    found = [
        f"{f.relative_to(SITE)}: {a['href']}"
        for f in _html_files()
        for a in BeautifulSoup(f.read_text(encoding="utf-8"), "html.parser").find_all("a", href=True)
        if a["href"].startswith(prefix)
    ]
    assert found == []
