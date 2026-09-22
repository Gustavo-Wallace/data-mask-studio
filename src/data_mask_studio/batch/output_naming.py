import os
from pathlib import Path
from data_mask_studio.batch.reservation import OutputReservation

from data_mask_studio.batch.exceptions import BatchStructuralError
from data_mask_studio.csv_tools.csv_anonymizer import paths_refer_to_same_file


def suggested_output_name(source: str | Path) -> str:
    path = Path(source)
    return f"{path.stem}_anonimizado.csv"


def reserve_output_path(output_directory: str | Path, source: str | Path) -> Path:
    """Compatibility API; batch processing retains the owned reservation instead."""
    reservation = reserve_output_file(output_directory, source)
    try:
        descriptor = os.open(reservation.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
        return reservation.path
    finally:
        reservation.close()


def reserve_output_file(output_directory: str | Path, source: str | Path) -> OutputReservation:
    directory = Path(output_directory).expanduser().absolute()
    source_path = Path(source).expanduser().absolute()
    stem = f"{source_path.stem}_anonimizado"
    index = 1
    while True:
        suffix = "" if index == 1 else f"_{index}"
        candidate = directory / f"{stem}{suffix}.csv"
        if paths_refer_to_same_file(source_path, candidate):
            index += 1
            continue
        try:
            reservation = OutputReservation(candidate)
            try:
                exists = candidate.exists()
            except BaseException:
                reservation.close()
                raise
            if exists:
                reservation.close()
                index += 1
                continue
        except FileExistsError:
            index += 1
            continue
        except OSError as error:
            raise BatchStructuralError(
                "Não foi possível reservar o arquivo de saída."
            ) from error
        return reservation
