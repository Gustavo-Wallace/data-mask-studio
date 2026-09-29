"""Package-only restoration. Construction authenticates and verifies binding."""

from pathlib import Path

from data_mask_studio.restoration.exceptions import MissingCodeError, RestorationSecurityError
from data_mask_studio.restoration.sources import ScalarValue, CompositeValue
from data_mask_studio.transfer_package.binding import verify_file_binding
from data_mask_studio.transfer_package.models import PackageError
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
