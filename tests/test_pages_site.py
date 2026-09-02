import json
import re
import tomllib
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
BASE_URL = "https://gustavo-wallace.github.io/data-mask-studio/"
PORTUGUESE_URL = f"{BASE_URL}pt-br/"
REPOSITORY_URL = "https://github.com/Gustavo-Wallace/data-mask-studio"
RELEASES_URL = f"{REPOSITORY_URL}/releases/latest"
LICENSE_URL = f"{REPOSITORY_URL}/blob/main/LICENSE"


class PageInspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.ids: set[str] = set()
        self.title_parts: list[str] = []
        self.heading_levels: list[int] = []
        self.json_ld_parts: list[str] = []
        self._inside_title = False
        self._inside_json_ld = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = {name: value or "" for name, value in attrs}
        self.tags.append((tag, attributes))
        if identifier := attributes.get("id"):
            self.ids.add(identifier)
        if tag == "title":
            self._inside_title = True
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.heading_levels.append(int(tag[1]))
        if tag == "script" and attributes.get("type") == "application/ld+json":
            self._inside_json_ld = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._inside_title = False
        if tag == "script":
            self._inside_json_ld = False

    def handle_data(self, data: str) -> None:
        if self._inside_title:
            self.title_parts.append(data)
        if self._inside_json_ld:
            self.json_ld_parts.append(data)

    def attributes_for(self, tag_name: str) -> list[dict[str, str]]:
        return [attributes for tag, attributes in self.tags if tag == tag_name]

    @property
    def title(self) -> str:
        return "".join(self.title_parts).strip()

    @property
    def structured_data(self) -> dict[str, object]:
        return json.loads("".join(self.json_ld_parts))


def inspect_page(path: Path) -> tuple[str, PageInspector]:
    content = path.read_text(encoding="utf-8")
    inspector = PageInspector()
    inspector.feed(content)
    return content, inspector


def meta_content(inspector: PageInspector, attribute: str, value: str) -> str:
    matches = [
        meta.get("content", "")
        for meta in inspector.attributes_for("meta")
        if meta.get(attribute) == value
    ]
    assert len(matches) == 1
    return matches[0]


def linked_url(inspector: PageInspector, rel: str, hreflang: str | None = None) -> str:
    matches = []
    for link in inspector.attributes_for("link"):
        relations = link.get("rel", "").split()
        if rel not in relations:
            continue
        if hreflang is not None and link.get("hreflang") != hreflang:
            continue
        matches.append(link.get("href", ""))
    assert len(matches) == 1
    return matches[0]


PAGES = (
    (
        DOCS / "index.html",
        "en",
        "Data Mask Studio (DMS) — Open Source Data Masking Tool",
        BASE_URL,
    ),
    (
        DOCS / "pt-br" / "index.html",
        "pt-BR",
        "Data Mask Studio (DMS) — Mascaramento de Dados para Windows",
        PORTUGUESE_URL,
    ),
)


@pytest.mark.parametrize("page,language,title,canonical", PAGES)
def test_pages_have_language_titles_descriptions_and_canonical(
    page: Path, language: str, title: str, canonical: str
) -> None:
    assert page.is_file()
    _, inspector = inspect_page(page)
    html = inspector.attributes_for("html")

    assert len(html) == 1
    assert html[0].get("lang") == language
    assert inspector.title == title
    assert "Data Mask Studio" in meta_content(inspector, "name", "description")
    assert linked_url(inspector, "canonical") == canonical


@pytest.mark.parametrize("page,language,title,canonical", PAGES)
def test_pages_declare_reciprocal_hreflang_and_open_graph(
    page: Path, language: str, title: str, canonical: str
) -> None:
    _, inspector = inspect_page(page)

    assert linked_url(inspector, "alternate", "en") == BASE_URL
    assert linked_url(inspector, "alternate", "pt-BR") == PORTUGUESE_URL
    assert linked_url(inspector, "alternate", "x-default") == BASE_URL
    assert meta_content(inspector, "property", "og:title") == title
    assert meta_content(inspector, "property", "og:type") == "website"
    assert meta_content(inspector, "property", "og:url") == canonical
    assert meta_content(inspector, "property", "og:site_name") == "Data Mask Studio"
    assert meta_content(inspector, "property", "og:image") == (
        f"{BASE_URL}assets/dms_icon_1024.png"
    )


@pytest.mark.parametrize("page,language,title,canonical", PAGES)
def test_pages_have_accessible_heading_structure_and_language_links(
    page: Path, language: str, title: str, canonical: str
) -> None:
    _, inspector = inspect_page(page)
    anchors = inspector.attributes_for("a")

    assert inspector.heading_levels.count(1) == 1
    assert inspector.heading_levels[0] == 1
    assert all(
        current <= previous + 1
        for previous, current in zip(
            inspector.heading_levels, inspector.heading_levels[1:]
        )
    )
    assert any(anchor.get("hreflang") == "en" for anchor in anchors)
    assert any(anchor.get("hreflang") == "pt-BR" for anchor in anchors)
    assert any(anchor.get("href", "").startswith("#") for anchor in anchors)
    assert inspector.attributes_for("main")
    assert inspector.attributes_for("nav")
    assert not inspector.attributes_for("form")


@pytest.mark.parametrize("page,language,title,canonical", PAGES)
def test_pages_reference_only_existing_internal_files_and_fragments(
    page: Path, language: str, title: str, canonical: str
) -> None:
    _, inspector = inspect_page(page)
    docs_root = DOCS.resolve()

    references: list[str] = []
    for tag, attributes in inspector.tags:
        if tag in {"a", "link"} and attributes.get("href"):
            references.append(attributes["href"])
        if tag == "img" and attributes.get("src"):
            references.append(attributes["src"])

    for reference in references:
        parsed = urlsplit(reference)
        if parsed.scheme or parsed.netloc:
            continue
        if parsed.path:
            target = (page.parent / parsed.path).resolve()
            assert target == docs_root or docs_root in target.parents
            if target.is_dir():
                target = target / "index.html"
            assert target.exists(), reference
        if parsed.fragment and not parsed.path:
            assert parsed.fragment in inspector.ids, reference


@pytest.mark.parametrize("page,language,title,canonical", PAGES)
def test_pages_use_truthful_software_application_structured_data(
    page: Path, language: str, title: str, canonical: str
) -> None:
    _, inspector = inspect_page(page)
    data = inspector.structured_data

    assert data["@context"] == "https://schema.org"
    assert data["@type"] == "SoftwareApplication"
    assert data["name"] == "Data Mask Studio"
    assert data["alternateName"] == "DMS"
    assert data["applicationCategory"] == "SecurityApplication"
    assert data["operatingSystem"] == "Windows"
    assert data["codeRepository"] == REPOSITORY_URL
    assert data["downloadUrl"] == RELEASES_URL
    assert data["license"] == "https://www.gnu.org/licenses/gpl-3.0.html"
    assert data["author"] == {
        "@type": "Person",
        "name": "Gustavo Wallace Macedo Santos",
        "url": "https://github.com/Gustavo-Wallace",
    }
    assert "aggregateRating" not in data
    assert "offers" not in data


@pytest.mark.parametrize("page,language,title,canonical", PAGES)
def test_pages_contain_public_project_links_license_and_author(
    page: Path, language: str, title: str, canonical: str
) -> None:
    content, inspector = inspect_page(page)
    hrefs = {anchor.get("href") for anchor in inspector.attributes_for("a")}

    assert REPOSITORY_URL in hrefs
    assert RELEASES_URL in hrefs
    assert LICENSE_URL in hrefs
    assert "https://github.com/Gustavo-Wallace" in hrefs
    assert "GPL-3.0-only" in content
    assert "Gustavo Wallace Macedo Santos" in content
    assert "HMAC-SHA256" in content
    assert "AES-256-GCM" in content
    assert "Windows DPAPI" in content


@pytest.mark.parametrize("page,language,title,canonical", PAGES)
def test_pages_have_no_trackers_remote_scripts_or_data_collection(
    page: Path, language: str, title: str, canonical: str
) -> None:
    content, inspector = inspect_page(page)
    normalized = content.casefold()
    forbidden = (
        "google-analytics",
        "googletagmanager",
        "gtag(",
        "facebook.com/tr",
        "connect.facebook.net",
        "hotjar",
        "segment.com",
        "mixpanel",
    )

    assert not any(value in normalized for value in forbidden)
    scripts = inspector.attributes_for("script")
    assert len(scripts) == 1
    assert scripts[0].get("type") == "application/ld+json"
    assert "src" not in scripts[0]
    assert not inspector.attributes_for("form")
    assert not inspector.attributes_for("iframe")


def test_sitemap_and_robots_publish_only_the_two_language_urls() -> None:
    sitemap = DOCS / "sitemap.xml"
    robots = (DOCS / "robots.txt").read_text(encoding="utf-8")
    namespace = {"sitemap": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locations = [
        element.text
        for element in ET.parse(sitemap).getroot().findall("sitemap:url/sitemap:loc", namespace)
    ]

    assert locations == [BASE_URL, PORTUGUESE_URL]
    assert "User-agent: *" in robots
    assert "Allow: /" in robots
    assert f"Sitemap: {BASE_URL}sitemap.xml" in robots


def test_site_reuses_official_assets_without_external_runtime_dependency() -> None:
    expected_assets = {
        DOCS / "assets" / "dms_icon.svg": ROOT / "assets" / "branding" / "dms_icon.svg",
        DOCS / "assets" / "dms_icon_1024.png": ROOT / "assets" / "branding" / "dms_icon_1024.png",
        DOCS / "assets" / "data-mask-studio-main.png": DOCS / "images" / "data-mask-studio-main.png",
    }

    for published, official in expected_assets.items():
        assert published.is_file()
        assert published.read_bytes() == official.read_bytes()
    stylesheet = (DOCS / "styles.css").read_text(encoding="utf-8")
    assert "@import" not in stylesheet
    assert "url(" not in stylesheet
    assert not (DOCS / "script.js").exists()


def test_product_screenshot_preserves_aspect_ratio_responsively() -> None:
    stylesheet = (DOCS / "styles.css").read_text(encoding="utf-8")
    rule = re.search(r"\.product-preview img\s*\{([^}]*)\}", stylesheet)

    assert rule is not None
    declarations = rule.group(1).casefold()
    assert "width: 100%" in declarations
    assert "height: auto" in declarations
    assert not re.search(r"height:\s*\d", declarations)
    container = re.search(r"\.product-preview\s*\{([^}]*)\}", stylesheet)
    assert container is not None
    assert "max-width: 100%" in container.group(1).casefold()

    for page, _language, _title, _canonical in PAGES:
        _, inspector = inspect_page(page)
        screenshots = [
            image
            for image in inspector.attributes_for("img")
            if image.get("src", "").endswith("data-mask-studio-main.png")
        ]
        assert len(screenshots) == 1
        assert screenshots[0].get("width") == "1280"
        assert screenshots[0].get("height") == "800"


def test_site_does_not_change_application_version() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == "1.1.0"


def test_github_pages_skips_jekyll_processing() -> None:
    assert (DOCS / ".nojekyll").is_file()


def test_public_site_uses_data_masking_language_consistently() -> None:
    english = (DOCS / "index.html").read_text(encoding="utf-8")
    portuguese = (DOCS / "pt-br" / "index.html").read_text(encoding="utf-8")

    assert "pseudonymization" not in english.casefold()
    assert "pseudonimização" not in portuguese.casefold()
    assert "data masking" in english.casefold()
    assert "mascaramento" in portuguese.casefold()
    assert (
        "deterministic data masking with controlled restoration through an "
        "encrypted local vault"
    ) in english.casefold()
    assert (
        "mascaramento determinístico com restauração controlada por meio de um "
        "cofre local criptografado"
    ) in portuguese.casefold()


def test_english_homepage_contains_google_site_verification() -> None:
    _, inspector = inspect_page(DOCS / "index.html")

    assert meta_content(inspector, "name", "google-site-verification") == (
        "2V_Hk5J_PDTpbsVzJw7ZUUb8-mQ_qrsIOJb8AKWhJMg"
    )
