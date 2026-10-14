import hashlib
from pathlib import Path

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


def test_readme_capture_is_safe_sized_and_dimensioned() -> None:
    capture = ROOT / "docs" / "images" / "data-mask-studio-main.png"

    assert capture.is_file()
    assert capture.stat().st_size < 600 * 1024
    image = QImage(str(capture))
    assert not image.isNull()
    assert 1200 <= image.width() <= 1320
    assert 740 <= image.height() <= 840


@pytest.mark.parametrize(
    ("filename", "required"),
    (
        (
            "README.md",
            (
                "Version 1.3.0 is in preparation and has not been published",
                "**Local vault:**",
                "**Selective transfer package:**",
                "**Full environment backup:**",
                "unchanged masked CSV",
                "8-character minimum password",
                "minimum 12 characters",
                "no fallback to the local vault",
                "not HTML or batch operations",
                "64 MiB",
            ),
        ),
        (
            "README.pt-BR.md",
            (
                "A versão 1.3.0 está em preparação e ainda não foi publicada",
                "**Cofre local:**",
                "**Pacote seletivo de transferência:**",
                "**Backup completo do ambiente:**",
                "CSV mascarado sem alterações",
                "senha de no mínimo 8 caracteres",
                "mínimo de 12 caracteres",
                "não há fallback para o cofre local",
                "não para HTML ou operações em lote",
                "64 MiB",
            ),
        ),
    ),
)
def test_readmes_describe_release_preparation_and_restoration_requirements(
    filename: str, required: tuple[str, ...]
) -> None:
    content = (ROOT / filename).read_text(encoding="utf-8")

    for phrase in required:
        assert phrase in content
    for contract in (".dmspackage", ".dmsbackup", "AES-256-GCM", "Windows DPAPI"):
        assert contract in content


def test_changelog_marks_1_3_0_as_unpublished_without_replacing_1_2_0() -> None:
    content = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    current, historical = content.split("## 1.2.0 — Data Preparation Update", 1)

    assert "## 1.3.0 — Selective Transfer Update (em preparação)" in current
    assert "Esta versão ainda não foi publicada." in current
    assert ".dmspackage" in current
    assert "## 1.1.0" in historical


def test_compatibility_distinguishes_transfer_and_backup_format_contracts() -> None:
    content = (ROOT / "COMPATIBILITY.md").read_text(encoding="utf-8")

    for contract in (
        "A versão 1.3.0 ainda não foi publicada",
        "schema 4", "Profile Format v2", "perfis v1 válidos",
        "formato v1 de backup `.dmsbackup`", "`.dmspackage` tem formato v1 próprio",
        "SHA-256 dos bytes", "não há substituição silenciosa da fonte",
        "64 MiB", "até 1 GiB", "a interface não oferece esse ajuste",
        "não garante que versões antigas consigam abrir um cofre já migrado",
        "dms-vault-compaction-*",
    ):
        assert contract in content


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
