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


def test_repository_has_neutral_licensing_notice_without_license_artifacts() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    expected = (
        "Este repositório ainda não possui uma licença geral de reutilização definida. "
        "A publicação do código no GitHub não concede, por si só, autorização ampla "
        "para copiar, modificar ou redistribuir o projeto."
    )

    assert expected in readme
    assert not (ROOT / "LICENSE").exists()
    assert not (ROOT / "LICENSE.md").exists()
    assert not (ROOT / "NOTICE").exists()
    assert "spdx" not in readme.casefold()
    assert "badge/license" not in readme.casefold()


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
