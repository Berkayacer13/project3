"""Layer 4 — QueryProcessor.

Parses input lines, dispatches to FileIndexManager, writes results to
output.txt, logs every operation to log.csv, and handles `stats`, `stats
reset`, and `explain`.

The QP receives references to all lower layers so it can READ COUNTERS for
stats and explain output. It does NOT bypass layers for data access.
"""

import os
import time

from buffer_manager import BufferManager
from disk_space_manager import DiskSpaceManager
from file_index_manager import FileIndexManager


class QueryProcessor:
    def __init__(
        self,
        config: dict,
        file_idx: FileIndexManager,
        buffer: BufferManager,
        disk: DiskSpaceManager,
    ):
        self.config = config
        self.file_idx = file_idx
        self.buffer = buffer
        self.disk = disk

        # All output files live next to archive.py — never derive from config.
        self.base_dir: str = config["_base_dir"]
        self.output_path = os.path.join(self.base_dir, "output.txt")
        self.log_path = os.path.join(self.base_dir, "log.csv")
        self.stats_output_path = os.path.join(self.base_dir, "stats_output.txt")

        # Open output.txt fresh per run (clean slate for each invocation).
        # log.csv is append-only and must survive restarts.
        open(self.output_path, "w").close()
        # Ensure log.csv exists (append mode creates it if missing).
        open(self.log_path, "a").close()

    # ----- Entry point called by archive.py -----

    def process(self, line: str) -> None:
        raise NotImplementedError

    # ----- Internal helpers (placeholders) -----

    def _write_output(self, text: str) -> None:
        with open(self.output_path, "a") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")

    def _log(self, line: str, status: str) -> None:
        ts = int(time.time())
        with open(self.log_path, "a") as f:
            f.write(f"{ts},{line},{status}\n")

    def _write_stats(self) -> None:
        raise NotImplementedError

    def _reset_stats(self) -> None:
        self.disk.reset_counts()
        self.buffer.reset_stats()
        self.file_idx.reset_stats()
