"""Workload generator for CMPE 321 Project 3 experiments.

Usage:
    python3 workload_generator.py --mode {sequential,random,range,mixed} \\
        --records N --queries Q [--seed S] [--type-name T] [--stats-reset]

Output is written to stdout so it can be piped or redirected to a file.

Modes:
    sequential  PKs 1..N inserted in order; queries search PKs 1..Q in order
    random      PKs drawn from a large pool (no dups); queries search random PKs
    range       PKs 1..N inserted; queries are range_search on 'value' field
    mixed       PKs 1..N inserted; 50% search_record + 50% range_search queries

With --stats-reset, the output wraps the query section with:
    stats reset   (before first query)
    stats         (after last query)
This isolates query-phase metrics from insertion-phase I/O.

Type schema (fixed):
    create type <T> 6 4 name str category str label str id int value int score int
    - 'id' is the primary key (field order 4, 1-indexed)
    - 'value' and 'score' are used for range queries
"""

import argparse
import random
import string
import sys


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FIELDS_DEF = "name str category str label str id int value int score int"
PK_ORDER = 4        # 1-indexed field order of 'id'
STR_LEN = 8         # length of generated string field values
VALUE_MAX = 9999    # maximum value for int 'value' and 'score' fields
RANGE_FRACTION = 5  # range queries cover ~1/5 of the value space


def _rand_str(rng: random.Random, length: int = STR_LEN) -> str:
    """Return a random alphanumeric string of the given length."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(rng.choices(alphabet, k=length))


# ---------------------------------------------------------------------------
# Record generators
# ---------------------------------------------------------------------------

def _gen_records_sequential(rng: random.Random, type_name: str, n: int):
    """Yield 'create record' lines with PKs 1..N."""
    for pk in range(1, n + 1):
        name = _rand_str(rng)
        category = _rand_str(rng)
        label = _rand_str(rng)
        value = rng.randint(0, VALUE_MAX)
        score = rng.randint(0, VALUE_MAX)
        yield f"create record {type_name} {name} {category} {label} {pk} {value} {score}"


def _gen_records_random(rng: random.Random, type_name: str, n: int):
    """Yield 'create record' lines with unique random PKs from a large pool."""
    pool_size = max(n * 10, n + 1000)
    pks = rng.sample(range(1, pool_size + 1), n)
    for pk in pks:
        name = _rand_str(rng)
        category = _rand_str(rng)
        label = _rand_str(rng)
        value = rng.randint(0, VALUE_MAX)
        score = rng.randint(0, VALUE_MAX)
        yield f"create record {type_name} {name} {category} {label} {pk} {value} {score}"


def _gen_records_sequential_with_values(rng: random.Random, type_name: str, n: int):
    """Yield records AND return the list of (pk, value) pairs for range query generation."""
    records = []
    lines = []
    for pk in range(1, n + 1):
        name = _rand_str(rng)
        category = _rand_str(rng)
        label = _rand_str(rng)
        value = rng.randint(0, VALUE_MAX)
        score = rng.randint(0, VALUE_MAX)
        lines.append(f"create record {type_name} {name} {category} {label} {pk} {value} {score}")
        records.append((pk, value))
    return lines, records


# ---------------------------------------------------------------------------
# Query generators
# ---------------------------------------------------------------------------

def _queries_sequential(rng: random.Random, type_name: str, n_records: int, q: int):
    """Search records with PKs 1..min(Q, N) in ascending order."""
    for i in range(1, min(q, n_records) + 1):
        yield f"search record {type_name} {i}"
    # If Q > N, cycle back from 1
    if q > n_records:
        for i in range(q - n_records):
            yield f"search record {type_name} {(i % n_records) + 1}"


def _queries_random_search(rng: random.Random, type_name: str, pks: list, q: int):
    """Search random PKs from the inserted set."""
    chosen = [rng.choice(pks) for _ in range(q)]
    for pk in chosen:
        yield f"search record {type_name} {pk}"


def _queries_range(rng: random.Random, type_name: str, q: int):
    """Generate range_search queries on the 'value' field with random windows."""
    step = VALUE_MAX // RANGE_FRACTION
    for _ in range(q):
        lo = rng.randint(0, VALUE_MAX - step)
        hi = lo + rng.randint(1, step)
        yield f"range_search {type_name} value {lo} {hi}"


def _queries_mixed(rng: random.Random, type_name: str, pks: list, q: int):
    """Alternate between search_record and range_search (50/50)."""
    step = VALUE_MAX // RANGE_FRACTION
    for i in range(q):
        if i % 2 == 0:
            pk = rng.choice(pks)
            yield f"search record {type_name} {pk}"
        else:
            lo = rng.randint(0, VALUE_MAX - step)
            hi = lo + rng.randint(1, step)
            yield f"range_search {type_name} value {lo} {hi}"


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate(mode: str, n_records: int, n_queries: int, seed: int,
             type_name: str, stats_reset: bool):
    """Write a complete workload to stdout."""
    rng = random.Random(seed)
    out = sys.stdout

    # Header: type definition
    out.write(f"create type {type_name} 6 {PK_ORDER} {FIELDS_DEF}\n")

    # Records
    if mode == "sequential":
        record_lines = list(_gen_records_sequential(rng, type_name, n_records))
        pks = list(range(1, n_records + 1))
    elif mode == "random":
        record_lines = list(_gen_records_random(rng, type_name, n_records))
        # Line: "create record <type> name category label id value score"
        # id is at split index 6 (0-indexed)
        pks = [int(line.split()[6]) for line in record_lines]
    elif mode == "range":
        record_lines, pairs = _gen_records_sequential_with_values(rng, type_name, n_records)
        pks = [p for p, _ in pairs]
    elif mode == "mixed":
        record_lines = list(_gen_records_sequential(rng, type_name, n_records))
        pks = list(range(1, n_records + 1))
    else:
        raise ValueError(f"Unknown mode: {mode}")

    for line in record_lines:
        out.write(line + "\n")

    # Optional stats reset before query phase
    if stats_reset:
        out.write("stats reset\n")

    # Queries
    if mode == "sequential":
        queries = _queries_sequential(rng, type_name, n_records, n_queries)
    elif mode == "random":
        queries = _queries_random_search(rng, type_name, pks, n_queries)
    elif mode == "range":
        queries = _queries_range(rng, type_name, n_queries)
    elif mode == "mixed":
        queries = _queries_mixed(rng, type_name, pks, n_queries)

    for q in queries:
        out.write(q + "\n")

    # Optional stats at end
    if stats_reset:
        out.write("stats\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate deterministic DBMS workloads for CMPE 321 Project 3"
    )
    parser.add_argument(
        "--mode",
        choices=["sequential", "random", "range", "mixed"],
        required=True,
        help="Query pattern to generate",
    )
    parser.add_argument(
        "--records",
        type=int,
        required=True,
        metavar="N",
        help="Number of records to insert",
    )
    parser.add_argument(
        "--queries",
        type=int,
        required=True,
        metavar="Q",
        help="Number of query lines to generate",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    parser.add_argument(
        "--type-name",
        default="experiment",
        metavar="T",
        help="Type name to use in the workload (default: experiment)",
    )
    parser.add_argument(
        "--stats-reset",
        action="store_true",
        help="Wrap queries with 'stats reset' ... 'stats' for experiment isolation",
    )
    args = parser.parse_args()

    generate(
        mode=args.mode,
        n_records=args.records,
        n_queries=args.queries,
        seed=args.seed,
        type_name=args.type_name,
        stats_reset=args.stats_reset,
    )


if __name__ == "__main__":
    main()
