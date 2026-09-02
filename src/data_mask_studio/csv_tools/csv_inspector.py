import codecs
import csv
from io import StringIO
from pathlib import Path

from data_mask_studio.csv_tools.encoding import (
    BOM_ENCODINGS,
    LATIN_1,
    UTF_8,
    WINDOWS_1252,
    python_codec,
)
from data_mask_studio.csv_tools.header_resolver import resolve_empty_headers
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
    delimiter = _detect_delimiter(decoded_sample)
    raw_headers = _read_headers(path, encoding, delimiter)
    headers, replacements = resolve_empty_headers(raw_headers)
    _validate_headers(headers)

    return CSVInspectionResult(
        path=path.resolve(),
        encoding=encoding,
        delimiter=delimiter,
        headers=headers,
        header_replacements=replacements,
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
    sample_is_complete = len(sample) < SAMPLE_SIZE
    for bom, encoding in BOM_ENCODINGS:
        if not sample.startswith(bom):
            continue
        try:
            decoded = _decode_sample(
                sample,
                python_codec(encoding),
                final=sample_is_complete,
            )
        except UnicodeDecodeError as error:
            raise CSVInspectionError(
                f"O arquivo declara {encoding.upper()} pelo BOM, "
                "mas seu conteúdo está inválido."
            ) from error
        return encoding, decoded

    if b"\x00" in sample:
        raise CSVInspectionError(
            "O arquivo contém bytes NUL e pode ser UTF-16 ou UTF-32 sem BOM, "
            "formato que não pode ser identificado com segurança."
        )

    for encoding, display_name in (
        (python_codec(UTF_8), UTF_8),
        (python_codec(WINDOWS_1252), WINDOWS_1252),
        (python_codec(LATIN_1), LATIN_1),
    ):
        try:
            return display_name, _decode_sample(
                sample,
                encoding,
                final=sample_is_complete,
            )
        except UnicodeDecodeError:
            continue

    raise CSVInspectionError("Não foi possível identificar a codificação do arquivo.")


def _decode_sample(sample: bytes, encoding: str, *, final: bool = False) -> str:
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    return decoder.decode(sample, final=final)


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
    try:
        with path.open("r", encoding=python_codec(encoding), newline="") as csv_file:
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
    if not headers:
        raise CSVInspectionError("O arquivo CSV não contém um cabeçalho válido.")
