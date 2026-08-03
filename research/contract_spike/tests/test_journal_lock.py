from __future__ import annotations

import multiprocessing
import tempfile
import unittest
from pathlib import Path

from ..journal_lock import append_handoff


def append_many(workpad: str, worker: int, count: int) -> None:
    for index in range(count):
        append_handoff(
            Path(workpad),
            f"worker-{worker}-{index}",
            f"worker={worker} index={index}\n".encode("ascii"),
        )


class JournalLockTests(unittest.TestCase):
    def test_concurrent_processes_allocate_one_strict_sequence(self) -> None:
        worker_count = 8
        writes_per_worker = 5
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as temporary:
            processes = [
                context.Process(
                    target=append_many,
                    args=(temporary, worker, writes_per_worker),
                )
                for worker in range(worker_count)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=20)
                self.assertEqual(process.exitcode, 0)

            names = sorted(path.name for path in (Path(temporary) / "handoffs").iterdir())
            sequences = [int(name.split("-", 1)[0]) for name in names]
            self.assertEqual(sequences, list(range(1, worker_count * writes_per_worker + 1)))
            self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
