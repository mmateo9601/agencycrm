from common.access import workspace_access

from .execution import claim_next, finish_failure, finish_success, update_progress
from .handlers import HANDLERS, ExecutionContext
from .locking import WorkerLock
from .models import Job
from .payloads import validate_payload


class JobRunner:
    """Synchronous local adapter; enqueue contracts need not change with a future dispatcher."""

    def run_once(self, *, kinds: list[str] | None = None) -> Job | None:
        """Process at most one due job while exclusively holding the database lock."""
        with WorkerLock() as lock:
            return self.run_locked(lock=lock, kinds=kinds)

    def run_locked(self, *, lock: WorkerLock, kinds: list[str] | None = None) -> Job | None:
        """Worker-loop entry point; no database transaction surrounds the handler."""
        job = claim_next(lock=lock, kinds=kinds)
        if job is None:
            return None
        try:
            if job.created_by is None:
                raise ValueError("Job creator no longer exists")
            workspace_access(user=job.created_by, workspace_id=job.workspace_id, write=True)
            payload = validate_payload(job.kind, job.payload)
            context = ExecutionContext(
                job_id=job.pk,
                workspace_id=job.workspace_id,
                attempt=job.attempts,
                payload=payload,
                progress=lambda current, total: update_progress(
                    job=job, current=current, total=total
                ),
            )
            result = HANDLERS[job.kind](context)
            finish_success(lock=lock, job=job, result=result)
        except Exception as error:
            # Deliberate task boundary: persist a sanitized failure and keep the worker alive.
            finish_failure(lock=lock, job=job, error=error)
        job.refresh_from_db()
        return job
