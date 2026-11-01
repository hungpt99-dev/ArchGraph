from __future__ import annotations

import logging

from .jobs import worker_loop

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
if __name__ == "__main__":
    worker_loop()
