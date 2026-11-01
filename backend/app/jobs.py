"""PostgreSQL-backed job queue + worker loop.

Jobs are claimed with ``FOR UPDATE SKIP LOCKED`` so several workers can run
concurrently without a broker. Retries use exponential backoff and a dead-letter
state after ``max_attempts``.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal
from .models import Job, new_id

log = logging.getLogger("archgraph.jobs")
settings = get_settings()

HANDLERS: dict[str, callable] = {}


def register(kind: str):
    def wrapper(fn):
        HANDLERS[kind] = fn
        return fn

    return wrapper


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(
    db: Session,
    kind: str,
    payload: dict,
    workspace_id: str | None = None,
    priority: int = 5,
    max_attempts: int = 3,
    run_after: datetime | None = None,
) -> Job:
    job = Job(
        id=new_id(), workspace_id=workspace_id, kind=kind, payload=payload,
        status="QUEUED", priority=priority, max_attempts=max_attempts,
        run_after=run_after or _now(),
    )
    db.add(job)
    db.flush()
    return job


def claim_next(db: Session, worker_id: str) -> Job | None:
    row = db.execute(
        text(
            """
            UPDATE jobs SET status='RUNNING', started_at=clock_timestamp(), worker=:worker,
                            attempts=attempts+1
            WHERE id = (
                SELECT id FROM jobs
                WHERE status='QUEUED' AND run_after <= clock_timestamp()
                ORDER BY priority ASC, created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id
            """
        ),
        {"worker": worker_id},
    ).first()
    db.commit()
    if not row:
        return None
    return db.get(Job, row[0])


def _progress_cb(db: Session, job: Job):
    def cb(pct: int, message: str = "") -> None:
        job.progress = max(0, min(100, pct))
        if message:
            job.message = message[:400]
        db.commit()

    return cb


def run_job(db: Session, job: Job) -> dict:
    if job.kind not in HANDLERS:
        from . import tasks  # noqa: F401  (register handlers on demand)
    handler = HANDLERS.get(job.kind)
    if handler is None:
        raise RuntimeError(f"No handler registered for job kind {job.kind}")
    log.info("job %s (%s) starting attempt %s", job.id, job.kind, job.attempts)
    result = handler(db, job, _progress_cb(db, job)) or {}
    job.status = "COMPLETED"
    job.progress = 100
    job.result = result if isinstance(result, dict) else {"result": result}
    job.finished_at = _now()
    db.commit()
    log.info("job %s (%s) completed", job.id, job.kind)
    return result


def fail_job(db: Session, job: Job, exc: Exception) -> None:
    db.rollback()
    job = db.get(Job, job.id) or job
    job.error = f"{type(exc).__name__}: {exc}"[:2000]
    if job.attempts >= job.max_attempts:
        job.status = "DEAD"
        job.finished_at = _now()
    else:
        job.status = "QUEUED"
        job.run_after = _now() + timedelta(seconds=min(300, 10 * (2 ** (job.attempts - 1))))
    db.commit()
    log.warning("job %s failed (attempt %s/%s): %s", job.id, job.attempts, job.max_attempts, exc)


def worker_loop(poll_interval: float | None = None, once: bool = False) -> None:
    from . import tasks  # noqa: F401  (registers handlers)

    poll_interval = poll_interval or settings.job_poll_interval
    worker_id = f"{socket.gethostname()}-{os.getpid()}-{threading.get_ident()}"
    stop = {"flag": False}

    def _sig(signum, frame):  # pragma: no cover
        log.info("worker received signal %s, shutting down", signum)
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    log.info("worker %s started", worker_id)
    heartbeat = os.getenv("WORKER_HEARTBEAT", "/tmp/archgraph-worker-heartbeat")
    idle = 0
    while not stop["flag"]:
        try:
            with open(heartbeat, "w", encoding="utf-8") as fh:
                fh.write(str(time.time()))
        except OSError:
            pass
        db = SessionLocal()
        try:
            job = claim_next(db, worker_id)
            if job is None:
                idle += 1
                if once and idle > 2:
                    break
                time.sleep(poll_interval)
                continue
            try:
                run_job(db, job)
            except Exception as exc:  # noqa: BLE001
                fail_job(db, job, exc)
                log.debug(traceback.format_exc())
        finally:
            db.close()
    log.info("worker %s stopped", worker_id)
