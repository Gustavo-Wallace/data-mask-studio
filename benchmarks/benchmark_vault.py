import argparse
import json
import time
import tracemalloc
from pathlib import Path

from data_mask_studio.anonymization import TokenGenerator
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark sintético do cofre.")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--operations", type=int, default=100_000)
    parser.add_argument("--unique-values", type=int, default=10_000)
    parser.add_argument("--batch-size", type=int, default=1_000)
    args = parser.parse_args()
    repository = VaultRepository(args.database, VaultCipher(b"V" * 32))
    generator = TokenGenerator(b"H" * 32)
    before = args.database.stat().st_size
    tracemalloc.start()
    started = time.perf_counter()
    with repository.transaction() as transaction:
        for offset in range(0, args.operations, args.batch_size):
            candidates = []
            for item in range(offset, min(offset + args.batch_size, args.operations)):
                value = f"SINTETICO_{item % args.unique_values:08d}"
                candidates.append(
                    MappingCandidate(
                        generator.generate("ID", value),
                        "ID",
                        value,
                        "COLUNA_1",
                        canonical_value=value,
                        normalization_rule=NormalizationRule.EXACT,
                    )
                )
            transaction.upsert_batch(candidates)
        summary = transaction.summary()
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(json.dumps({
        "synthetic": True,
        "operations": args.operations,
        "unique_values": args.unique_values,
        "repetitions": max(0, args.operations - args.unique_values),
        "batch_size": args.batch_size,
        "operations_per_second": args.operations / elapsed,
        "seconds": elapsed,
        "peak_memory_mib": peak / 1_048_576,
        "new_mappings": summary.new_mappings,
        "updated_mappings": summary.updated_mappings,
        "vault_growth_bytes": args.database.stat().st_size - before,
    }, indent=2))


if __name__ == "__main__":
    main()
