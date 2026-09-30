"""Package-only restoration. Construction authenticates and verifies binding."""

from pathlib import Path
import hashlib
import tempfile
from contextlib import contextmanager
from collections.abc import Sequence

from data_mask_studio.restoration.exceptions import MissingCodeError, RestorationSecurityError
from data_mask_studio.restoration.sources import ScalarValue, CompositeValue
from data_mask_studio.transfer_package.binding import verify_file_binding, verify_binding
from data_mask_studio.transfer_package.models import PackageError, MaskedFileBinding
from data_mask_studio.transfer_package.serialization import MAX_PAYLOAD_SIZE
from data_mask_studio.transfer_package.service import read_package


class TransferPackageRestorationSource:
    __slots__ = ("_scalars", "_composites")

    def __init__(self, package_path: str | Path, password: str, masked_csv_path: str | Path,
                 *, max_payload_bytes: int = MAX_PAYLOAD_SIZE):
        try:
            payload = read_package(package_path, password, max_payload_bytes=max_payload_bytes)
            verify_file_binding(masked_csv_path, payload.masked_file)
        except PackageError:
            raise RestorationSecurityError(
                "Não foi possível autenticar o pacote e verificar seu vínculo com o CSV."
            ) from None
        # Index references, not copies of plaintext mapping collections. Neither
        # password, payload container nor derived keys are retained by the source.
        self._initialize(payload)

    def _initialize(self, payload):
        self._scalars = {mapping.code: mapping for mapping in payload.scalar_mappings}
        self._composites = {mapping.code: mapping for mapping in payload.composite_mappings}

    def _lookup(self, code: str, *, composite: bool):
        expected, other = ((self._composites, self._scalars) if composite
                           else (self._scalars, self._composites))
        if code in other:
            raise RestorationSecurityError("Tipo de mapeamento incompatível com a consulta.")
        mapping = expected.get(code)
        if mapping is None:
            raise MissingCodeError("Mapeamento requerido ausente na fonte de restauração.")
        return mapping

    def get_scalar(self, code: str) -> ScalarValue:
        mapping = self._lookup(code, composite=False)
        return ScalarValue(mapping.code, mapping.original_value, mapping.canonical_value)

    def get_composite(self, code: str) -> CompositeValue:
        mapping = self._lookup(code, composite=True)
        return CompositeValue(mapping.code, mapping.identity_version,
                              mapping.canonical_values, mapping.original_values)

    def get_many_with_composites(self, codes: Sequence[str]) -> dict[str, ScalarValue | CompositeValue]:
        result = {}
        for code in dict.fromkeys(codes):
            if code in self._composites:
                result[code] = self.get_composite(code)
            else:
                result[code] = self.get_scalar(code)
        return result


@contextmanager
def open_verified_package_input(package_path, password, masked_csv_path, *,
                                max_payload_bytes=MAX_PAYLOAD_SIZE, should_cancel=None):
    """Yield a source and the same handle whose copied bytes were verified.

    Only masked input is spooled. TemporaryFile retains ownership and deletes on
    close, including exception/cancellation paths; no reopening by pathname.
    """
    from data_mask_studio.restoration.analyzer import _raise_if_cancelled

    try:
        _raise_if_cancelled(should_cancel)
        payload = read_package(package_path, password, max_payload_bytes=max_payload_bytes)
        _raise_if_cancelled(should_cancel)
        with tempfile.TemporaryFile(mode="w+b", prefix=".dms-verified-input-") as snapshot:
            digest, size = hashlib.sha256(), 0
            with Path(masked_csv_path).open("rb") as original:
                while block := original.read(1024 * 1024):
                    _raise_if_cancelled(should_cancel)
                    snapshot.write(block)
                    digest.update(block)
                    size += len(block)
                    if size > payload.masked_file.size:
                        raise PackageError("O CSV excede o tamanho vinculado ao pacote.")
            snapshot.flush()
            verify_binding(MaskedFileBinding(digest.hexdigest(), size), payload.masked_file)
            snapshot.seek(0)
            source = TransferPackageRestorationSource.__new__(TransferPackageRestorationSource)
            source._initialize(payload)
            del payload
            _raise_if_cancelled(should_cancel)
            yield source, snapshot
    except (PackageError, OSError):
        raise RestorationSecurityError("Não foi possível verificar o pacote e seu CSV com segurança.") from None
