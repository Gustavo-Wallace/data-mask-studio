from pathlib import Path


def available_output_path(source: Path, output_directory: Path) -> Path:
    stem = source.stem
    if stem.casefold().endswith("_anonimizado"):
        stem = stem[: -len("_anonimizado")]
    extension = ".csv" if source.suffix.casefold() == ".csv" else ".html"
    base = output_directory / f"{stem}_restaurado{extension}"
    if not base.exists() and not _same_path(source, base):
        return base
    sequence = 2
    while True:
        candidate = output_directory / f"{stem}_restaurado_{sequence}{extension}"
        if not candidate.exists() and not _same_path(source, candidate):
            return candidate
        sequence += 1


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve().samefile(right.resolve())
    except (FileNotFoundError, OSError):
        return left.resolve() == right.resolve()
