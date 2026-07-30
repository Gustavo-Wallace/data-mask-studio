from collections.abc import Callable

from data_mask_studio.consultant.code_parser import is_valid_code, parse_codes
from data_mask_studio.consultant.models import (
    ConsultationResult,
    ConsultationStatus,
)
from data_mask_studio.vault.repository import VaultRepository

VaultRepositoryFactory = Callable[[], VaultRepository]


class ConsultantService:
    """Coordena consultas exatas sem expor falhas criptográficas."""

    def __init__(self, repository_factory: VaultRepositoryFactory) -> None:
        self._repository_factory = repository_factory
        self.last_error: Exception | None = None

    def consult(self, raw_text: str) -> list[ConsultationResult]:
        self.last_error = None
        repository: VaultRepository | None = None
        repository_error: Exception | None = None
        results: list[ConsultationResult] = []

        for code in parse_codes(raw_text):
            if not is_valid_code(code):
                results.append(
                    ConsultationResult(
                        code,
                        ConsultationStatus.INVALID,
                        message="Formato de código inválido.",
                    )
                )
                continue

            if repository is None and repository_error is None:
                try:
                    repository = self._repository_factory()
                except Exception as error:
                    repository_error = error
                    self.last_error = error

            if repository_error is not None or repository is None:
                results.append(_recovery_failure(code))
                continue

            try:
                mapping = repository.get_decrypted_mapping(code)
            except Exception as error:
                self.last_error = error
                results.append(_recovery_failure(code))
            else:
                if mapping is None:
                    results.append(
                        ConsultationResult(
                            code,
                            ConsultationStatus.NOT_FOUND,
                            message="Código não encontrado no cofre.",
                        )
                    )
                else:
                    results.append(
                        ConsultationResult(code, ConsultationStatus.FOUND, mapping)
                    )

        return results


def _recovery_failure(code: str) -> ConsultationResult:
    return ConsultationResult(
        code,
        ConsultationStatus.RECOVERY_FAILED,
        message="Não foi possível recuperar este mapeamento com segurança.",
    )
