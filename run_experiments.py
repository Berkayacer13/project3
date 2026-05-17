"""Experiment runner for CMPE 321 Project 3.

Runs all three required experiments and prints tabulated results to stdout.
Also writes results to experiment_results.txt for inclusion in the report.

Experiments:
    1  LRU vs MRU  — sequential and random workloads
    2  Index strategies  — heap_scan, hash_index, bplus_tree
    3  Buffer pool size sensitivity  — 4, 8, 16, 32, 64

Usage:
    python3 run_experiments.py [--records N] [--queries Q] [--seed S]

Each experiment run:
  - generates a workload (via workload_generator.py)
  - writes a temporary config
  - clears all data/output files next to archive.py
  - runs archive.py
  - parses stats_output.txt
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.join(HERE, "archive.py")
BASE_CONFIG = os.path.join(HERE, "config.json")
STATS_FILE = os.path.join(HERE, "stats_output.txt")
GENERATOR = os.path.join(HERE, "workload_generator.py")

PYTHON = sys.executable

# Data files that must be cleared between runs (glob-style match done manually)
DATA_EXTS = {".dat", ".idx"}
EXTRA_FILES = ["catalog.dat", "output.txt", "log.csv", "stats_output.txt"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_data():
    """Remove all *.dat, *.idx, catalog.dat, output.txt, log.csv, stats_output.txt."""
    for fname in os.listdir(HERE):
        fpath = os.path.join(HERE, fname)
        if os.path.isfile(fpath):
            ext = os.path.splitext(fname)[1]
            if ext in DATA_EXTS or fname in EXTRA_FILES:
                os.remove(fpath)


def _write_config(overrides: dict) -> str:
    """Write a temporary config file and return its path."""
    with open(BASE_CONFIG) as f:
        cfg = json.load(f)
    cfg.update(overrides)
    # Remove runtime-only key if present
    cfg.pop("_base_dir", None)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, dir=HERE
    )
    json.dump(cfg, tmp)
    tmp.close()
    return tmp.name


def _generate_workload(mode: str, n_records: int, n_queries: int, seed: int) -> str:
    """Generate a workload file and return its path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, dir=HERE
    )
    tmp.close()
    cmd = [
        PYTHON, GENERATOR,
        "--mode", mode,
        "--records", str(n_records),
        "--queries", str(n_queries),
        "--seed", str(seed),
        "--stats-reset",
    ]
    with open(tmp.name, "w") as out:
        subprocess.run(cmd, stdout=out, check=True)
    return tmp.name


def _run_archive(config_path: str, workload_path: str) -> None:
    """Run archive.py with the given config and workload."""
    subprocess.run(
        [PYTHON, ARCHIVE, config_path, workload_path],
        check=True,
        cwd=HERE,
    )


def _parse_stats() -> dict:
    """Parse stats_output.txt into a plain dict."""
    result = {}
    if not os.path.exists(STATS_FILE):
        return result
    with open(STATS_FILE) as f:
        content = f.read()

    m = re.search(r"Disk I/O:\s+(\d+) reads,\s+(\d+) writes", content)
    if m:
        result["reads"] = int(m.group(1))
        result["writes"] = int(m.group(2))

    m = re.search(
        r"Buffer Pool:\s+(\d+) requests,\s+(\d+) hits,\s+(\d+) misses\s+\(([0-9.]+)% hit rate\)",
        content,
    )
    if m:
        result["requests"] = int(m.group(1))
        result["hits"] = int(m.group(2))
        result["misses"] = int(m.group(3))
        result["hit_rate"] = float(m.group(4))

    m = re.search(r"Evictions:\s+(\d+)\s+\((\d+) dirty writebacks\)", content)
    if m:
        result["evictions"] = int(m.group(1))
        result["dirty_writebacks"] = int(m.group(2))

    m = re.search(r"Index:\s+(\S+),\s+(\d+) nodes visited", content)
    if m:
        result["strategy"] = m.group(1)
        result["nodes_visited"] = int(m.group(2))

    m = re.search(r"Records:\s+(\d+) scanned,\s+(\d+) returned", content)
    if m:
        result["records_scanned"] = int(m.group(1))
        result["records_returned"] = int(m.group(2))

    return result


def _run_one(label: str, config_overrides: dict, mode: str,
             n_records: int, n_queries: int, seed: int) -> dict:
    """Full pipeline: clear → generate workload → write config → run → parse."""
    print(f"  Running: {label} ...", flush=True)
    _clear_data()
    cfg_path = _write_config(config_overrides)
    wl_path = _generate_workload(mode, n_records, n_queries, seed)
    try:
        _run_archive(cfg_path, wl_path)
        stats = _parse_stats()
    finally:
        os.remove(cfg_path)
        os.remove(wl_path)
    return stats


# ---------------------------------------------------------------------------
# Table printer
# ---------------------------------------------------------------------------

def _table(title: str, headers: list, rows: list) -> str:
    """Return a plain-text table string."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    def fmt_row(cells):
        return "  ".join(str(c).ljust(col_widths[i]) for i, c in enumerate(cells))

    sep = "  ".join("-" * w for w in col_widths)
    lines = [f"\n=== {title} ===", fmt_row(headers), sep]
    for row in rows:
        lines.append(fmt_row(row))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------

def experiment1(n_records: int, n_queries: int, seed: int) -> str:
    """LRU vs MRU on sequential and random workloads."""
    print("\n[Experiment 1] LRU vs MRU")
    base = {"buffer_pool_size": 16, "index_strategy": "bplus_tree"}
    configs = [
        ("Sequential + LRU", "sequential", {**base, "replacement_policy": "LRU"}),
        ("Sequential + MRU", "sequential", {**base, "replacement_policy": "MRU"}),
        ("Random     + LRU", "random",     {**base, "replacement_policy": "LRU"}),
        ("Random     + MRU", "random",     {**base, "replacement_policy": "MRU"}),
    ]
    rows = []
    for label, mode, cfg in configs:
        s = _run_one(label, cfg, mode, n_records, n_queries, seed)
        rows.append([
            label,
            s.get("reads", "?"),
            s.get("writes", "?"),
            f"{s.get('hit_rate', 0.0):.1f}%",
            s.get("evictions", "?"),
        ])

    return _table(
        "Experiment 1: LRU vs MRU (buffer_pool_size=16, index=bplus_tree)",
        ["Workload + Policy", "Disk Reads", "Disk Writes", "Hit Rate", "Evictions"],
        rows,
    )


def experiment2(n_records: int, n_queries: int, seed: int) -> str:
    """Index strategy comparison on mixed workload."""
    print("\n[Experiment 2] Index strategies")
    base = {"buffer_pool_size": 16, "replacement_policy": "LRU"}
    configs = [
        ("heap_scan",  {**base, "index_strategy": "heap_scan"}),
        ("hash_index", {**base, "index_strategy": "hash_index"}),
        ("bplus_tree", {**base, "index_strategy": "bplus_tree"}),
    ]
    rows = []
    for label, cfg in configs:
        s = _run_one(label, cfg, "mixed", n_records, n_queries, seed)
        rows.append([
            label,
            s.get("reads", "?"),
            s.get("writes", "?"),
            s.get("nodes_visited", "?"),
            s.get("records_scanned", "?"),
            s.get("records_returned", "?"),
        ])

    return _table(
        "Experiment 2: Index Strategies (buffer_pool_size=16, policy=LRU, mode=mixed)",
        ["Strategy", "Disk Reads", "Disk Writes", "Nodes Visited", "Recs Scanned", "Recs Returned"],
        rows,
    )


def experiment3(n_records: int, n_queries: int, seed: int) -> str:
    """Buffer pool size sensitivity on random workload."""
    print("\n[Experiment 3] Buffer pool size sensitivity")
    base = {"replacement_policy": "LRU", "index_strategy": "bplus_tree"}
    sizes = [4, 8, 16, 32, 64]
    rows = []
    for sz in sizes:
        cfg = {**base, "buffer_pool_size": sz}
        label = f"pool={sz:2d}"
        s = _run_one(label, cfg, "random", n_records, n_queries, seed)
        rows.append([
            sz,
            s.get("reads", "?"),
            s.get("writes", "?"),
            f"{s.get('hit_rate', 0.0):.1f}%",
            s.get("evictions", "?"),
            s.get("dirty_writebacks", "?"),
        ])

    return _table(
        "Experiment 3: Buffer Pool Size (policy=LRU, index=bplus_tree, mode=random)",
        ["Pool Size", "Disk Reads", "Disk Writes", "Hit Rate", "Evictions", "Dirty WBs"],
        rows,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run CMPE 321 Project 3 experiments")
    parser.add_argument("--records", type=int, default=1000,
                        help="Records to insert per run (default: 1000)")
    parser.add_argument("--queries", type=int, default=200,
                        help="Queries per run (default: 200)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed (default: 42)")
    args = parser.parse_args()

    print(f"Experiment parameters: {args.records} records, {args.queries} queries, seed={args.seed}")

    tables = []
    tables.append(experiment1(args.records, args.queries, args.seed))
    tables.append(experiment2(args.records, args.queries, args.seed))
    tables.append(experiment3(args.records, args.queries, args.seed))

    output = "\n".join(tables) + "\n"
    print(output)

    results_path = os.path.join(HERE, "experiment_results.txt")
    with open(results_path, "w") as f:
        f.write(f"Experiment parameters: {args.records} records, "
                f"{args.queries} queries, seed={args.seed}\n")
        f.write(output)
    print(f"\nResults also saved to: {results_path}")

    # Restore the original data directory to a clean state
    _clear_data()


if __name__ == "__main__":
    main()
