# CMPE 321 Project 3 — Modular DBMS Engine

A four-layer disk-based database engine implemented in pure Python (standard library only).

## Requirements

- Python 3.8 or later
- No external packages — only the Python standard library is used

## Architecture

```
QueryProcessor   (Layer 4 — parses input, writes output.txt, logs to log.csv)
      |
FileIndexManager (Layer 3 — records, slotted pages, catalog, indexes)
      |
BufferManager    (Layer 2 — page cache with LRU or MRU eviction)
      |
DiskSpaceManager (Layer 1 — the only layer that performs file I/O)
```

## Quick Start

```bash
python3 archive.py config.json input.txt
```

Output files are created next to `archive.py`:
- `output.txt` — query results (truncated at the start of each run)
- `log.csv` — append-only operation log with timestamps and status
- `stats_output.txt` — snapshot of current statistics (overwritten by `stats` command)

## Configuration (`config.json`)

| Field | Type | Description | Example values |
|-------|------|-------------|----------------|
| `page_size` | int | Page size in bytes | `4096` |
| `max_records_per_page` | int | Maximum records per page (≤ 10) | `10` |
| `buffer_pool_size` | int | Number of frames in the buffer pool | `4`, `8`, `16`, `32`, `64` |
| `replacement_policy` | str | Page eviction strategy | `"LRU"`, `"MRU"` |
| `index_strategy` | str | Index type for new types | `"heap_scan"`, `"hash_index"`, `"bplus_tree"` |

Example:
```json
{
  "page_size": 4096,
  "max_records_per_page": 10,
  "buffer_pool_size": 16,
  "replacement_policy": "LRU",
  "index_strategy": "bplus_tree"
}
```

## Input Command Format

```
create type <name> <num_fields> <pk_order> [<field_name> <field_type>]...
create record <type_name> <val1> <val2> ...
search record <type_name> <pk_value>
delete record <type_name> <pk_value>
range_search <type_name> <field_name> <low> <high>
explain <command>
stats
stats reset
```

Field types: `int` or `str`. `pk_order` is 1-indexed.

## Running Tests

```bash
python3 tests/test_phase1.py   # 14 tests: DiskSpaceManager + BufferManager
python3 tests/test_phase2.py   # 16 tests: heap operations + QueryProcessor
python3 tests/test_phase3.py   # 14 tests: hash index + B+-tree + persistence
```

All 44 tests should pass.

## Smoke Test

```bash
rm -f *.dat *.idx catalog.dat output.txt log.csv stats_output.txt
python3 archive.py config.json tests/inputs/basic.txt
cat output.txt
cat stats_output.txt
```

## Workload Generator

```bash
python3 workload_generator.py --mode {sequential,random,range,mixed} \
    --records N --queries Q [--seed S] [--stats-reset]
```

Options:
- `--mode`: query pattern (see below)
- `--records N`: number of `create record` lines
- `--queries Q`: number of query lines
- `--seed S`: random seed for reproducibility (default: 42)
- `--stats-reset`: wrap queries with `stats reset` / `stats` for experiment isolation

Modes:
| Mode | Record insertion | Query pattern |
|------|-----------------|---------------|
| `sequential` | PKs 1..N in order | `search record` for PKs 1..Q in order |
| `random` | Random unique PKs | `search record` for random PKs |
| `range` | PKs 1..N in order | `range_search` on `value` field |
| `mixed` | PKs 1..N in order | 50% `search record` + 50% `range_search` |

Example:
```bash
python3 workload_generator.py --mode sequential --records 1000 --queries 200 \
    --stats-reset > workload.txt
python3 archive.py config.json workload.txt
```

## Running Experiments

```bash
python3 run_experiments.py [--records N] [--queries Q] [--seed S]
```

Runs all three experiments and prints tabulated results. Also saves results to `experiment_results.txt`.

Default: 1000 records, 200 queries, seed=42.

## Data Persistence

Data files (`.dat`, `.idx`, `catalog.dat`) persist across runs in the project directory.
To start fresh:
```bash
rm -f *.dat *.idx catalog.dat output.txt log.csv stats_output.txt
```

## File Structure

```
project3/
├── archive.py               — entry point
├── config.json              — configuration
├── workload_generator.py    — workload generator
├── run_experiments.py       — experiment runner
├── common/
│   └── results.py           — shared Result dataclasses
├── disk_space_manager/      — Layer 1
├── buffer_manager/          — Layer 2
├── file_index_manager/      — Layer 3
├── query_processor/         — Layer 4
└── tests/                   — unit + integration tests
```
