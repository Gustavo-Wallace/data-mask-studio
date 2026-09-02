import codecs

UTF_8 = "utf-8"
UTF_8_SIG = "utf-8-sig"
UTF_16_LE = "utf-16-le"
UTF_16_BE = "utf-16-be"
UTF_32_LE = "utf-32-le"
UTF_32_BE = "utf-32-be"
WINDOWS_1252 = "windows-1252"
LATIN_1 = "latin-1"

BOM_ENCODINGS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, UTF_32_LE),
    (codecs.BOM_UTF32_BE, UTF_32_BE),
    (codecs.BOM_UTF16_LE, UTF_16_LE),
    (codecs.BOM_UTF16_BE, UTF_16_BE),
    (codecs.BOM_UTF8, UTF_8_SIG),
)

_PYTHON_CODECS = {
    UTF_8: "utf-8",
    UTF_8_SIG: "utf-8-sig",
    UTF_16_LE: "utf-16",
    UTF_16_BE: "utf-16",
    UTF_32_LE: "utf-32",
    UTF_32_BE: "utf-32",
    WINDOWS_1252: "cp1252",
    LATIN_1: "latin-1",
}


def python_codec(encoding: str) -> str:
    """Retorna o codec de leitura que consome corretamente um BOM conhecido."""
    return _PYTHON_CODECS.get(encoding, encoding)
