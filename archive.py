import os
import sys
import json

from disk_space_manager import DiskSpaceManager
from buffer_manager import BufferManager
from file_index_manager import FileIndexManager
from query_processor import QueryProcessor


def main():
    config_path = sys.argv[1]
    input_path = sys.argv[2]
    with open(config_path) as cf:
        config = json.load(cf)

    # All output files (output.txt, stats_output.txt, log.csv) and all data
    # files (relations, indexes, catalog) live next to archive.py — never in
    # cwd, never in a temp folder, never in a config-derived path.
    config["_base_dir"] = os.path.dirname(os.path.abspath(__file__))

    # Build layers bottom-up. Each layer receives the one below it.
    disk = DiskSpaceManager(config)
    buffer = BufferManager(config, disk)
    file_idx = FileIndexManager(config, buffer)
    qp = QueryProcessor(config, file_idx, buffer, disk)

    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if line:
                qp.process(line)

    buffer.flush()


if __name__ == "__main__":
    main()
