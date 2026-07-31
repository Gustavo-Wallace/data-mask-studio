import codecs
from pathlib import Path

from data_mask_studio.html_restoration.exceptions import HTMLRestorationError

SAMPLE_SIZE = 64 * 1024


def inspect_html(file_path: str | Path) -> "HTMLInspectionResult":
    from data_mask_studio.html_restoration.models import HTMLInspectionResult

    path = Path(file_path).expanduser()
    try:
        if not path.is_file():
            raise HTMLRestorationError("O arquivo HTML selecionado não existe.")
        with path.open("rb") as html_file:
            sample = html_file.read(SAMPLE_SIZE)
    except HTMLRestorationError:
        raise
    except PermissionError as error:
        raise HTMLRestorationError(
            "Não foi possível ler o arquivo por falta de permissão."
        ) from error
    except OSError as error:
        raise HTMLRestorationError(
            "Não foi possível acessar o arquivo HTML selecionado."
        ) from error

    if not sample:
        raise HTMLRestorationError("O arquivo HTML está vazio.")
    encoding = detect_html_encoding(sample)
    return HTMLInspectionResult(path.resolve(), encoding)


def detect_html_encoding(sample: bytes) -> str:
    if sample.startswith(codecs.BOM_UTF8):
        _decode_sample(sample, "utf-8-sig")
        return "utf-8-sig"
    for codec_name, display_name in (
        ("utf-8", "utf-8"),
        ("cp1252", "windows-1252"),
        ("latin-1", "latin-1"),
    ):
        try:
            _decode_sample(sample, codec_name)
        except UnicodeDecodeError:
            continue
        return display_name
    raise HTMLRestorationError(
        "Não foi possível identificar a codificação do arquivo HTML."
    )


def python_encoding(encoding: str) -> str:
    return "cp1252" if encoding == "windows-1252" else encoding


def _decode_sample(sample: bytes, encoding: str) -> None:
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    decoder.decode(sample, final=False)
