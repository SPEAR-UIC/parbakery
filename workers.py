"""parbake/workers.py - one worker process per file, progress drawn by the parent

Workers put messages on a queue; the parent applies them to the display.
Message: (kind, name, payload, seconds)
    copying    payload 0
    estimate   payload: expected rows
    start      payload 0
    progress   payload: rows read
    finished   payload: dict of ProgressDisplay.update() fields
Standard library only: workers importing this do not load pandas.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import multiprocessing
import os
import queue
import sys
import time
from concurrent.futures import ProcessPoolExecutor

from settings import DEFAULT_WORKER_LIMIT, POLL_SECONDS


def worker_count(requested, file_count):
    """Return the worker count: requested or default, capped at file_count.
    Warn on stderr when a requested count is more than the CPUs."""
    cpus = os.cpu_count() or 1
    if requested is None:
        requested = min(DEFAULT_WORKER_LIMIT, cpus)
    count = max(1, min(requested, file_count))
    if count > cpus:
        # Allowed: reading from Lustre is mostly waiting, so more can be faster.
        print(f"warning: {count} workers on {cpus} CPUs. Each holds a batch of rows in "
              "memory and, with --batch-local-copies, a whole copy of its file on local "
              "disk; too many can run the node out of either.", file=sys.stderr)
    return count


def pool_context():
    """Return the forkserver context, or spawn where unavailable."""
    # Not fork: forking a process with threads can deadlock.
    try:
        return multiprocessing.get_context("forkserver")
    except ValueError:
        return multiprocessing.get_context("spawn")


def describe_error(problem):
    """Return "Type: message" for an exception."""
    return f"{type(problem).__name__}: {problem}"


def send(messages, name, kind, payload=0, seconds=0.0):
    """Put one progress message on the queue."""
    messages.put((kind, name, payload, seconds))


def apply(display, message):
    """Apply one message to the display."""
    kind, name, payload, seconds = message
    if kind == "copying":
        display.update(name, state="copying")
    elif kind == "estimate":
        display.update(name, estimated_rows=payload)
    elif kind == "start":
        display.update(name, state="reading")
    elif kind == "progress":
        display.update(name, state="reading", rows_read=payload, seconds=seconds)
    elif kind == "finished":
        display.update(name, **payload)


def drain(messages, display):
    """Apply every queued message, without blocking."""
    while True:
        try:
            apply(display, messages.get_nowait())
        except queue.Empty:
            return


def run_pool(work, jobs, workers, display, on_done=None):
    """Run work(job, messages) for each job; return the futures in job order.
    on_done(job, future) runs in the parent after each job's messages are drawn."""
    context = pool_context()
    with context.Manager() as manager:
        messages = manager.Queue()
        with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
            futures = [pool.submit(work, job, messages) for job in jobs]
            pending = dict(zip(futures, jobs))
            while pending:
                finished = [future for future in pending if future.done()]
                # Drain after collecting: a late message would reopen a finished job.
                drain(messages, display)
                for future in finished:
                    job = pending.pop(future)
                    if on_done is not None:
                        on_done(job, future)
                display.draw()
                time.sleep(POLL_SECONDS)
    return futures
