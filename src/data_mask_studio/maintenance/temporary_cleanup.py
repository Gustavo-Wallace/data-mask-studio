import os
import re
import shutil
import time
from collections.abc import Iterable
from pathlib import Path

from data_mask_studio.maintenance.models import CleanupResult, TemporaryItem

MINIMUM_AGE_SECONDS = 60 * 60
# Sufixo gerado por tempfile no runtime atual. Formatos futuros desconhecidos
# ficam preservados. Nomes genéricos .<destino>.<random>.tmp não provam autoria.
_RANDOM = r"[a-z0-9_]{8}"
_DIRECTORY_NAME = re.compile(r"\.dms-(?:backup|restore)-" + _RANDOM)
_FILE_NAME = re.compile(
    r"(?:\.dms-(?:write|access)-" + _RANDOM
    + r"|\..+\.dmsbackup\." + _RANDOM + r"\.tmp)"
)
_LOCAL_FILE_NAME = re.compile(
    r"(?:\.secret-" + _RANDOM + r"\.tmp|\.profiles\.json\." + _RANDOM + r"\.tmp)"
)


def locate_temporaries(
    application_directory: Path,
    session_directories: Iterable[Path] = (),
    *,
    now: float | None = None,
) -> list[TemporaryItem]:
    current_time = time.time() if now is None else now
    roots = _controlled_roots(application_directory, session_directories)
    found: list[TemporaryItem] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            candidates = list(root.iterdir())
        except OSError:
            continue
        for path in candidates:
            if not _recognized(path, roots[0]):
                continue
            key = str(path.resolve()).casefold()
            if key in seen:
                continue
            seen.add(key)
            try:
                stat = path.stat()
                age = max(0.0, current_time - stat.st_mtime)
                size = _size(path)
                in_use = _probably_in_use(path)
            except OSError:
                continue
            found.append(
                TemporaryItem(
                    path.resolve(),
                    size,
                    age,
                    age < MINIMUM_AGE_SECONDS,
                    in_use,
                )
            )
    return sorted(found, key=lambda item: str(item.path).casefold())


def cleanup_temporaries(
    items: Iterable[TemporaryItem],
    application_directory: Path,
    session_directories: Iterable[Path] = (),
) -> CleanupResult:
    roots = _controlled_roots(application_directory, session_directories)
    removed = preserved = failed = recovered = 0
    for item in items:
        if not item.selected or not item.removable:
            item.result = "Preservado"
            preserved += 1
            continue
        path = item.path.absolute()
        if not _inside_roots(path, roots) or not _recognized(path, roots[0]):
            item.result = "Preservado por segurança"
            preserved += 1
            continue
        try:
            if not path.exists():
                item.result = "Já não existe"
                preserved += 1
            elif time.time() - path.stat().st_mtime < MINIMUM_AGE_SECONDS:
                item.recent = True
                item.result = "Preservado por ser recente"
                preserved += 1
            elif _probably_in_use(path):
                item.in_use = True
                item.result = "Possivelmente em uso"
                preserved += 1
            elif path.is_dir():
                shutil.rmtree(path)
                item.result = "Removido"
                removed += 1
                recovered += item.size
            else:
                path.unlink()
                item.result = "Removido"
                removed += 1
                recovered += item.size
        except OSError:
            item.result = "Falha ao remover"
            failed += 1
    return CleanupResult(removed, preserved, failed, recovered)


def _controlled_roots(
    application_directory: Path, session_directories: Iterable[Path]
) -> tuple[Path, ...]:
    roots: list[Path] = []
    for value in (application_directory, *session_directories):
        path = Path(value).expanduser().absolute().resolve()
        if path not in roots:
            roots.append(path)
    return tuple(roots)


def _inside_roots(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path.parent == root for root in roots)


def _recognized(path: Path, application_directory: Path) -> bool:
    if path.is_symlink() or path.is_junction():
        return False
    name = path.name
    if path.is_dir():
        return _DIRECTORY_NAME.fullmatch(name) is not None
    return path.is_file() and (
        _FILE_NAME.fullmatch(name) is not None
        or (path.parent == application_directory and _LOCAL_FILE_NAME.fullmatch(name) is not None)
    )


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                pass
    return total


def _probably_in_use(path: Path) -> bool:
    # Apenas veto por permissão; acesso permitido não prova ausência de uso.
    # Não há locking: falhas de remoção do sistema operacional são preservadas.
    return not os.access(path, os.W_OK)
