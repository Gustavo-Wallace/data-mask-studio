import hashlib
from pathlib import Path

from PySide6.QtGui import QImage


ROOT = Path(__file__).resolve().parents[1]


def test_readme_has_compact_branding_download_and_documentation() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert 'src="assets/branding/dms_icon.svg"' in readme
    assert 'width="128"' in readme
    assert "dms_icon_1024.png" not in readme
    assert "https://github.com/Gustavo-Wallace/data-mask-studio/releases/latest" in readme
    assert "DataMaskStudio-Setup-<versão>.exe" in readme
    assert "DataMaskStudio-Portable-<versão>.zip" in readme
    for document in ("SECURITY.md", "PRIVACY.md", "COMPATIBILITY.md", "CHANGELOG.md"):
        assert f"]({document})" in readme


def test_readme_capture_is_safe_sized_and_dimensioned() -> None:
    capture = ROOT / "docs" / "images" / "data-mask-studio-main.png"

    assert capture.is_file()
    assert capture.stat().st_size < 600 * 1024
    image = QImage(str(capture))
    assert not image.isNull()
    assert 1200 <= image.width() <= 1320
    assert 740 <= image.height() <= 840


def test_repository_declares_gpl_3_0_only_and_includes_official_license() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    license_path = ROOT / "LICENSE"
    license_text = license_path.read_text(encoding="utf-8")
    normalized_license = license_text.replace("\r\n", "\n").encode("utf-8")

    assert "GNU General Public License v3.0 only" in readme
    assert "](LICENSE)" in readme
    assert "GPL-3.0-only" in readme
    assert "license-GPL--3.0--only" in readme
    assert "não possui uma licença geral" not in readme
    assert "não concede, por si só" not in readme
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
