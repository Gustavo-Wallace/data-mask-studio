import hashlib
from pathlib import Path
import re

import pytest
from PySide6.QtGui import QImage


ROOT = Path(__file__).resolve().parents[1]


def test_bilingual_readmes_have_navigation_branding_and_public_links() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    portuguese = (ROOT / "README.pt-BR.md").read_text(encoding="utf-8")

    assert english.startswith("English | [Português (Brasil)](README.pt-BR.md)")
    assert portuguese.startswith("[English](README.md) | Português (Brasil)")
    for readme in (english, portuguese):
        assert 'src="assets/branding/dms_icon.svg"' in readme
        assert 'width="128"' in readme
        assert "dms_icon_1024.png" not in readme
        assert "Data Mask Studio (DMS)" in readme
        assert "https://gustavo-wallace.github.io/data-mask-studio/" in readme
        assert "https://github.com/Gustavo-Wallace/data-mask-studio" in readme
        assert "https://github.com/Gustavo-Wallace/data-mask-studio/releases/latest" in readme
        assert "GPL-3.0-only" in readme
        assert "Copyright © 2026 Gustavo Wallace Macedo Santos" in readme
        for document in (
            "SECURITY.md",
            "PRIVACY.md",
            "COMPATIBILITY.md",
            "CHANGELOG.md",
        ):
            assert f"]({document})" in readme
    assert "DataMaskStudio-Setup-<version>.exe" in english
    assert "DataMaskStudio-Portable-<version>.zip" in english
    assert "DataMaskStudio-Setup-<versão>.exe" in portuguese
    assert "DataMaskStudio-Portable-<versão>.zip" in portuguese
    assert "pseudonymization, not irreversible anonymization" in english
    assert "pseudonimização, não anonimização irreversível" in portuguese
    assert "data masking" in english.casefold()
    assert "mascaramento" in portuguese.casefold()



SCREENSHOTS = tuple(
    f"data-mask-studio-{page}{suffix}.png"
    for suffix in ("", "-pt-br") for page in ("main", "batch", "package")
)


@pytest.mark.parametrize("filename", SCREENSHOTS)
def test_readme_capture_is_safe_sized_and_dimensioned(filename: str) -> None:
    capture = ROOT / "docs" / "images" / filename
    assert capture.is_file()
    assert capture.stat().st_size < 600 * 1024
    image = QImage(str(capture))
    assert not image.isNull()
    assert (image.width(), image.height()) == ((1600, 1000) if "package" in filename else (1280, 820))
    assert image.textKeys() == []  # No private paths in embedded metadata.


@pytest.mark.parametrize("filename", ["README.md", "README.pt-BR.md"])
def test_readmes_use_three_current_screenshots_in_their_own_language(filename: str) -> None:
    content = (ROOT / filename).read_text(encoding="utf-8")
    screenshots = re.findall(r'<img src="(docs/images/[^"]+)" alt="([^"]+)"', content)
    suffix = "-pt-br" if filename == "README.pt-BR.md" else ""
    assert {path for path, _alt in screenshots} == {
        f"docs/images/data-mask-studio-{page}{suffix}.png" for page in ("main", "batch", "package")
    }
    assert len(screenshots) == 3
    for path, alt in screenshots:
        assert (ROOT / path).is_file() and alt
    assert "<details>" in content and "</details>" in content
    assert "1.4.0" in content


@pytest.mark.parametrize(("filename", "required"), (
    ("README.md", (
        "Version 1.4.0 retains vault schema 4 and Base32-12 tokens",
        "package format v1, introduced in 1.3.0", "**Local vault:**",
        "**Selective transfer package:**", "**Full environment backup:**",
        "unchanged masked CSV", "8-character minimum password", "minimum 12 characters",
        "no fallback to the local vault", "not HTML or batch operations", "64 MiB",
        "In “Restore file”, select a CSV or HTML/HTM", "HTML uses the local vault",
        "Vault and maintenance → Overview → Update diagnostic",
        "Individual file publication is atomic", "recoverable, not jointly atomic",
        "System, Light or Dark", "Configuration Undo/Redo", "identifier restrictions",
    )),
    ("README.pt-BR.md", (
        "A versão 1.4.0 mantém o schema 4 do cofre e tokens Base32-12",
        "formato v1 do pacote, introduzido na 1.3.0", "**Cofre local:**",
        "**Pacote seletivo de transferência:**", "**Backup completo do ambiente:**",
        "CSV mascarado sem alterações", "senha de no mínimo 8 caracteres", "mínimo de 12 caracteres",
        "não há fallback para o cofre local", "não para HTML ou operações em lote", "64 MiB",
        "Em “Restaurar arquivo”, selecione um CSV ou HTML/HTM", "HTML usa o cofre local",
        "Cofre e manutenção → Visão geral → Atualizar diagnóstico",
        "A publicação de cada arquivo é atômica", "recuperável, não conjuntamente atômico",
        "Sistema, Claro ou Escuro", "Desfazer/refazer", "restrições de identificadores",
    )),
))
def test_readmes_describe_current_capabilities_and_restoration_requirements(filename, required):
    content = (ROOT / filename).read_text(encoding="utf-8")
    for phrase in required:
        assert phrase in content
    for contract in (".dmspackage", ".dmsbackup", "AES-256-GCM", "Windows DPAPI"):
        assert contract in content
    assert "/releases/latest" in content and "/releases/download/" not in content
    assert "HEX-24" not in content  # Detailed restrictions belong in COMPATIBILITY.md.
    assert "“Restore CSV” tab" not in content and "aba “Restaurar CSV”" not in content
    assert "“Restore HTML” tab" not in content and "aba “Restaurar HTML”" not in content
    assert not re.search(r"DataMaskStudio-(?:Setup|Portable)-[0-9]", content)


def test_changelog_preserves_release_order_historical_facts_and_bullet_structure():
    content = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    # Protect release attribution and compatibility facts, not entire sentences
    # or the old pre-cleanup bullet counts. Counts follow the approved style.
    expected = {
        "1.3.0 — Selective Transfer Update": (
            (4, 6), (".dmspackage", "recipient", "vault", "concurrent writes", "profile", "snapshot", "compaction"),
        ),
        "1.2.0 — Data Preparation Update": (
            (4, 6), ("preserve", "mask", "exclude", "composite", "profile format v2", "schema 4", "html", "recoverable"),
        ),
        "1.1.0": (
            (4, 6), ("normalization", "headers", "utf-16 le/be", "utf-32 le/be", "bom", "restoration"),
        ),
        "1.0.4": ((2, 4), ("html", "readme", "portuguese", "screenshot")),
        "1.0.3": ((2, 4), ("dark", "windows", "about", "gpl-3.0-only")),
        "1.0.2": ((2, 4), ("single-column", "missing", "extra", "progress", "html")),
        "1.0.1": ((2, 4), ("readme", "about", "bug-report")),
        "1.0.0": ((4, 6), ("stable", "csv", "html", "vault", "backup", "tokens", "schema 3", "installer")),
        "Earlier milestones": ((6, 6), ("schema 3", "recovery", "vault")),
    }
    entries = re.split(r"^## ", content, flags=re.MULTILINE)[2:]
    assert [entry.splitlines()[0] for entry in entries] == list(expected)
    for entry in entries:
        heading, *body = entry.splitlines()
        lines = [line for line in body if line and line != '<a id="marcos-anteriores"></a>']
        assert all(line.startswith("- ") for line in lines), heading
        (minimum, maximum), facts = expected[heading]
        assert minimum <= len(lines) <= maximum, heading
        for fact in facts:
            assert fact in "\n".join(lines).casefold(), (heading, fact)
    assert re.findall(r"^- (0\.[^:]+):", entries[-1], flags=re.MULTILINE) == [
        "0.11.x", "0.10.x", "0.9.x", "0.8.x", "0.5.x–0.7.x", "0.1.x–0.4.x",
    ]
    assert '<a id="marcos-anteriores"></a>' in content


def test_changelog_uses_one_flat_current_release_entry():
    content = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    current = content.split("## 1.3.0", 1)[0]
    assert current.startswith("# Changelog\n\n## 1.4.0 — Internationalization and User Experience Update\n\n")
    assert "### " not in content and "Unreleased" not in content
    bullets = [line for line in current.splitlines() if line.startswith("- ")]
    assert len(bullets) == 8
    assert all(len(line) <= 100 for line in bullets)
    topics = (
        ("english", "brazilian portuguese", "language"),
        ("system", "light", "dark"),
        ("csv", "html", "restoration"),
        ("drag-and-drop", "batch"),
        ("column search", "action filters", "contextual help"),
        ("read-only", "column structure"),
        ("configuration", "undo/redo", "normalization"),
        ("navigation", "keyboard shortcuts"),
    )
    for topic in topics:
        assert any(all(term in bullet.casefold() for term in topic) for bullet in bullets), topic


def test_compatibility_distinguishes_transfer_and_backup_format_contracts():
    content = (ROOT / "COMPATIBILITY.md").read_text(encoding="utf-8")
    for contract in (
        "## Version 1.4.0", "vault schema 4", "Profile Format v2", "valid v1 profiles",
        "backup format v1", "uses its own format v1", "SHA-256", "64 MiB", "up to 1 GiB",
        "interface does not offer", "does not guarantee that older application versions",
        "dms-vault-compaction-*", "HEX-24", "including authenticated packages accepted by the earlier codec",
        "during `.dmspackage` export and import", "entire package is rejected before restoration",
        "without filtering, converting or truncating", "Package restoration of HEX-24 identifiers is not supported",
        "Base32-12 tokens remain unchanged", "does not migrate or rewrite existing vaults",
        "Package format v1 and its encryption remain unchanged",
        "Vault and maintenance → Overview → Update diagnostic",
        "Individual files use atomic creation or replacement", "recoverable, not a single atomic transaction",
        "one published file does not prove that both were published",
    ):
        assert contract in content


@pytest.mark.parametrize("filename", [
    "README.md", "README.pt-BR.md", "CHANGELOG.md", "COMPATIBILITY.md", "PRIVACY.md", "SECURITY.md",
])
def test_public_documentation_uses_final_release_wording(filename):
    content = (ROOT / filename).read_text(encoding="utf-8").casefold()
    for phrase in ("em preparação", "ainda não lançada", "prevista", "ainda não publicada",
                   "coming soon", "upcoming", "in preparation", "not yet released", "unreleased"):
        assert phrase not in content


def test_security_policy_tracks_published_stable_releases_and_private_reports():
    content = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    for phrase in (
        "latest stable version actually published through GitHub Releases",
        "Older release series are not guaranteed to receive backported fixes",
        "Do not open a public issue", "privately", "Security Advisories",
        "Never submit real vaults, keys, CSV or HTML files",
        "`.dmsbackup` backups or `.dmspackage` packages", "synthetic data",
        "without including sensitive data",
    ):
        assert phrase in content
    assert "1.0.x" not in content and "1.3.0" not in content


def test_markdown_inventory_has_one_portuguese_exception():
    import subprocess

    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    expected = {
        "CHANGELOG.md": "# Changelog", "COMPATIBILITY.md": "# Compatibility and recovery",
        "PRIVACY.md": "# Privacy", "SECURITY.md": "# Security",
        "README.md": "English |", "README.pt-BR.md": "[English](README.md) | Português (Brasil)",
    }
    assert {name for name in tracked if Path(name).suffix.lower() == ".md"} == set(expected)
    for filename, opening in expected.items():
        content = (ROOT / filename).read_text(encoding="utf-8")
        assert re.sub(r'^<a id="[^"]+"></a>\n\n', "", content).startswith(opening)
    privacy = (ROOT / "PRIVACY.md").read_text(encoding="utf-8")
    assert "no telemetry, user accounts, synchronization or automatic uploads" in privacy
    assert "%LOCALAPPDATA%\\DataMaskStudio" in privacy
    assert "do not include local keys or every vault mapping" in privacy
    assert "original values, complete codes, keys and passwords" in privacy


def test_repository_declares_gpl_3_0_only_and_includes_official_license() -> None:
    readmes = "\n".join(
        (ROOT / name).read_text(encoding="utf-8")
        for name in ("README.md", "README.pt-BR.md")
    )
    license_path = ROOT / "LICENSE"
    license_text = license_path.read_text(encoding="utf-8")
    normalized_license = license_text.replace("\r\n", "\n").encode("utf-8")

    assert "GNU General Public License v3.0 only" in readmes
    assert "](LICENSE)" in readmes
    assert "GPL-3.0-only" in readmes
    assert "license-GPL--3.0--only" in readmes
    assert "Copyright © 2026 Gustavo Wallace Macedo Santos" in readmes
    assert "não possui uma licença geral" not in readmes
    assert "não concede, por si só" not in readmes
    assert license_path.is_file()
    assert not (ROOT / "LICENSE.md").exists()
    assert not (ROOT / "NOTICE").exists()
    assert license_text.startswith("                    GNU GENERAL PUBLIC LICENSE")
    assert "Version 3, 29 June 2007" in license_text
    assert hashlib.sha256(normalized_license).hexdigest() == (
        "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"
    )


def test_bug_report_template_requires_safe_public_information() -> None:
    content = (ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml").read_text(
        encoding="utf-8"
    )

    for value in (
        "Versão do Data Mask Studio",
        "Setup",
        "Portable",
        "Código-fonte",
        "Versão do Windows",
        "Descrição resumida",
        "Passos mínimos para reproduzir",
        "Comportamento esperado",
        "Comportamento observado",
        "dados sintéticos ou devidamente anonimizados",
        "vault.db",
        "CPF, nome, IP, e-mail, telefone",
        "issue semelhante",
        "vulnerabilidades não devem ser relatadas em issue pública",
        "SECURITY.md",
    ):
        assert value in content
    assert content.count("required: true") >= 12
    assert "type: file" not in content
