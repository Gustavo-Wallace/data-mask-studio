import codecs
import csv
from io import StringIO
from pathlib import Path

from data_mask_studio.csv_tools.models import CSVInspectionResult

SAMPLE_SIZE = 64 * 1024
SUPPORTED_DELIMITERS = ",;\t|"


class CSVInspectionError(Exception):
    """Erro esperado durante a inspeção de um CSV."""


def inspect_csv(file_path: str | Path) -> CSVInspectionResult:
    """Detecta o formato e lê somente o cabeçalho de um arquivo CSV."""
    path = Path(file_path).expanduser()
    sample = _read_sample(path)

    if not sample:
        raise CSVInspectionError("O arquivo CSV está vazio.")

    encoding, decoded_sample = _detect_encoding(sample)
    if not decoded_sample.strip():
        raise CSVInspectionError("O arquivo CSV não contém um cabeçalho válido.")

    delimiter = _detect_delimiter(decoded_sample)
    headers = _read_headers(path, encoding, delimiter)
    _validate_headers(headers)

    return CSVInspectionResult(
        path=path.resolve(),
        encoding=encoding,
        delimiter=delimiter,
        headers=headers,
    )


def _read_sample(path: Path) -> bytes:
    try:
        if not path.is_file():
            raise CSVInspectionError("O arquivo selecionado não existe.")
        with path.open("rb") as csv_file:
            return csv_file.read(SAMPLE_SIZE)
    except CSVInspectionError:
        raise
    except PermissionError as error:
        raise CSVInspectionError(
            "Não foi possível ler o arquivo por falta de permissão."
        ) from error
    except OSError as error:
        raise CSVInspectionError("Não foi possível acessar o arquivo selecionado.") from error


def _detect_encoding(sample: bytes) -> tuple[str, str]:
    if sample.startswith(codecs.BOM_UTF8):
        try:
            return "utf-8-sig", _decode_sample(sample, "utf-8-sig")
        except UnicodeDecodeError as error:
            raise CSVInspectionError(
                "O arquivo possui uma codificação UTF-8 inválida."
            ) from error

    for encoding, display_name in (
        ("utf-8", "utf-8"),
        ("cp1252", "windows-1252"),
        ("latin-1", "latin-1"),
    ):
        try:
            return display_name, _decode_sample(sample, encoding)
        except UnicodeDecodeError:
            continue

    raise CSVInspectionError("Não foi possível identificar a codificação do arquivo.")


def _decode_sample(sample: bytes, encoding: str) -> str:
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    return decoder.decode(sample, final=False)


def _detect_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=SUPPORTED_DELIMITERS)
    except csv.Error as error:
        if _is_valid_single_column_sample(sample):
            return ","
        raise CSVInspectionError(
            "Não foi possível identificar um separador CSV válido."
        ) from error
    return dialect.delimiter


def _is_valid_single_column_sample(sample: str) -> bool:
    """Reconhece CSV sem delimitador sem aceitar estruturas ambíguas ou inválidas."""
    if any(delimiter in sample for delimiter in SUPPORTED_DELIMITERS):
        return False
    try:
        rows = list(csv.reader(StringIO(sample), delimiter=",", strict=True))
    except csv.Error:
        return False
    return bool(rows) and all(len(row) <= 1 for row in rows)


def _read_headers(path: Path, encoding: str, delimiter: str) -> list[str]:
    python_encoding = "cp1252" if encoding == "windows-1252" else encoding
    try:
        with path.open("r", encoding=python_encoding, newline="") as csv_file:
            reader = csv.reader(csv_file, delimiter=delimiter, strict=True)
            return next(reader)
    except StopIteration as error:
        raise CSVInspectionError("O arquivo CSV não contém um cabeçalho válido.") from error
    except UnicodeDecodeError as error:
        raise CSVInspectionError(
            "O conteúdo do arquivo não corresponde à codificação detectada."
        ) from error
    except csv.Error as error:
        raise CSVInspectionError("O cabeçalho do CSV possui formato inválido.") from error
    except PermissionError as error:
        raise CSVInspectionError(
            "Não foi possível ler o arquivo por falta de permissão."
        ) from error
    except OSError as error:
        raise CSVInspectionError("Não foi possível ler o arquivo selecionado.") from error


def _validate_headers(headers: list[str]) -> None:
    if not headers or any(not header.strip() for header in headers):
        raise CSVInspectionError(
            "O arquivo CSV possui um ou mais cabeçalhos vazios."
        )
