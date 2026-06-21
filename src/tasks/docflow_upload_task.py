from infrastructure.celery import celery


@celery.task(name="docflow.finalize_upload")
def finalize_upload_task(upload_session_id: str) -> dict[str, str]:
    """Phase 3 queue boundary; object assembly is deliberately deferred."""
    return {"upload_session_id": upload_session_id, "status": "queued_for_finalization"}
