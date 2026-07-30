import ctypes
from ctypes import wintypes

CRYPTPROTECT_UI_FORBIDDEN = 0x01


class DPAPIError(RuntimeError):
    """Falha ao proteger ou recuperar dados com o Windows DPAPI."""


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class WindowsDPAPIProtector:
    """Protege dados para o usuário atual do Windows."""

    def protect(self, data: bytes) -> bytes:
        return _run_dpapi("CryptProtectData", data)

    def unprotect(self, data: bytes) -> bytes:
        return _run_dpapi("CryptUnprotectData", data)


def _run_dpapi(function_name: str, data: bytes) -> bytes:
    if not data:
        raise DPAPIError("O conteúdo a proteger não pode estar vazio.")

    input_buffer = ctypes.create_string_buffer(data)
    input_blob = _DataBlob(
        len(data),
        ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output_blob = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    function = getattr(crypt32, function_name)
    function.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    function.restype = wintypes.BOOL

    succeeded = function(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    if not succeeded:
        error_code = ctypes.get_last_error()
        raise DPAPIError(f"Falha do Windows DPAPI (código {error_code}).")

    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(output_blob.pbData)
