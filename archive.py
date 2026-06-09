import os
import sys
import json

from disk_space_manager import DiskSpaceManager
from buffer_manager import BufferManager
from file_index_manager import FileIndexManager
from query_processor import QueryProcessor
from recovery_manager import RecoveryManager


def main():
    config_path = sys.argv[1]
    input_path = sys.argv[2]
    with open(config_path) as cf:
        config = json.load(cf)

    # All output files (output.txt, log.csv, wal.log, master.rec) and all data
    # files (relations, indexes, catalog) live next to archive.py — never in
    # cwd, never in a temp folder, never in a config-derived path.
    config["_base_dir"] = os.path.dirname(os.path.abspath(__file__))

    # Build layers bottom-up. Recovery is a cross-cutting concern (spec §3): it
    # sits beside the stack — owned by no single layer — and is wired into both
    # the DiskSpaceManager (below) and the BufferManager (cross-cutting).
    disk = DiskSpaceManager(config)
    recovery = RecoveryManager(config, disk)
    buffer = BufferManager(config, disk)
    buffer.set_recovery(recovery)
    recovery.set_buffer(buffer)
    file_idx = FileIndexManager(config, buffer)
    qp = QueryProcessor(config, file_idx, buffer, disk, recovery)

    # Three-phase ARIES recovery runs BEFORE any input is processed (spec §5),
    # reconstructing a consistent state from the WAL after any prior crash.
    recovery.recover()
    # Indexes are derived structures: rebuild them from the recovered data so
    # they are always consistent with the records, regardless of the crash point.
    file_idx.rebuild_indexes()

    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if line:
                qp.process(line)

    # Clean shutdown: persist dirty pages (WAL-respecting) and checkpoint so the
    # next restart has a short, well-defined log to scan. A `crash` command exits
    # via os._exit(1) before reaching here, so none of this runs on a crash.
    buffer.flush()
    recovery.checkpoint()


if __name__ == "__main__":
    main()
