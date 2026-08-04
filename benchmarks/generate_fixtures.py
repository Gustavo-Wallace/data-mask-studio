import argparse
import csv
import sys
import time
from pathlib import Path


def generate_csv(path: Path, rows: int, unique_values: int, columns: int) -> None:
    if rows <= 0 or unique_values <= 0 or columns <= 0:
        raise ValueError("Linhas, valores únicos e colunas devem ser positivos.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="", buffering=1_048_576) as file:
        writer = csv.writer(file)
        writer.writerow([f"COLUNA_{index + 1}" for index in range(columns)])
        started = time.perf_counter()
        progress_interval = max(10_000, rows // 20)
        for row in range(rows):
            writer.writerow(
                f"SINTETICO_{column}_{row % unique_values:08d}"
                for column in range(columns)
            )
            if (row + 1) % progress_interval == 0 or row + 1 == rows:
                print(
                    f"Geração da fixture: {row + 1:,}/{rows:,} linhas "
                    f"({time.perf_counter() - started:.1f}s)",
                    file=sys.stderr,
                    flush=True,
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera CSV sintético para benchmarks.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=10_000)
    parser.add_argument("--unique-values", type=int, default=1_000)
    parser.add_argument("--columns", type=int, default=2)
    args = parser.parse_args()
    generate_csv(args.output, args.rows, args.unique_values, args.columns)


if __name__ == "__main__":
    main()
