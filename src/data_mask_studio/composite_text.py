"""Representação textual compartilhada, independente de processamento e UI."""
import json


def composite_text(values: tuple[str, ...]) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))
