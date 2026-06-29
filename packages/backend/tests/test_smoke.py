"""Smoke tests for the TOM backend package and product branding."""

from backend.tom import __version__, branding


def test_tom_version_is_set() -> None:
    assert isinstance(__version__, str)
    assert __version__ == "0.1.0"


def test_branding_product_name() -> None:
    assert branding.PRODUCT_NAME == "TOM"


def test_branding_version_matches_package() -> None:
    assert __version__ == branding.VERSION


def test_branding_api_version_is_v1() -> None:
    assert branding.API_VERSION == "v1"


def test_branding_license_is_mit() -> None:
    assert branding.LICENSE_NAME == "MIT"


def test_branding_links_are_https() -> None:
    for url in (branding.DOCS_URL, branding.REPO_URL):
        assert url.startswith("https://"), f"{url} must be https"
