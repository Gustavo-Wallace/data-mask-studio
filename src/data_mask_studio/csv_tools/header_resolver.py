from collections.abc import Sequence

from data_mask_studio.csv_tools.models import CSVHeaderReplacement


def resolve_empty_headers(
    headers: Sequence[str],
) -> tuple[list[str], tuple[CSVHeaderReplacement, ...]]:
    """Substitui headers vazios sem alterar headers válidos ou criar colisões."""
    occupied = {header for header in headers if header.strip()}
    resolved: list[str] = []
    replacements: list[CSVHeaderReplacement] = []

    for position, header in enumerate(headers, start=1):
        if header.strip():
            resolved.append(header)
            continue

        base_name = f"column_{position}"
        candidate = base_name
        suffix = 2
        while candidate in occupied:
            candidate = f"{base_name}_{suffix}"
            suffix += 1
        occupied.add(candidate)
        resolved.append(candidate)
        replacements.append(CSVHeaderReplacement(position, candidate))

    return resolved, tuple(replacements)


def format_header_replacement_warning(
    replacements: Sequence[CSVHeaderReplacement],
) -> str:
    if not replacements:
        return ""
    count = len(replacements)
    subject = (
        "cabeçalho vazio foi substituído"
        if count == 1
        else "cabeçalhos vazios foram substituídos"
    )
    details = "; ".join(
        f"coluna {replacement.position} → {replacement.synthetic_name}"
        for replacement in replacements
    )
    return f"{count} {subject}: {details}."
