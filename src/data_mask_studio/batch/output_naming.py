import os
from pathlib import Path

from data_mask_studio.batch.exceptions import BatchStructuralError
from data_mask_studio.csv_tools.csv_anonymizer import paths_refer_to_same_file


def suggested_output_name(source: str | Path) -> str:
    path = Path(source)
    return f"{path.stem}_anonimizado.csv"


def reserve_output_path(output_directory: str | Path, source: str | Path) -> Path:
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
            descriptor = os.open(
                candidate,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            index += 1
            continue
        except OSError as error:
            raise BatchStructuralError(
                "Não foi possível reservar o arquivo de saída."
            ) from error
        os.close(descriptor)
        return candidate
