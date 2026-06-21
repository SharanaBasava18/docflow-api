import hashlib
from datetime import datetime, timezone
from tempfile import SpooledTemporaryFile
from uuid import UUID

from sqlalchemy import select

from entities.file import File, FileStatus
from entities.upload_session import FileChunk, UploadSession, UploadSessionStatus
from infrastructure.celery import celery
from infrastructure.db.postgresql import SessionLocal
from infrastructure.minio import minioStorage


def _failure_message(error: Exception) -> str:
    return str(error)[:1000] or error.__class__.__name__


def _mark_finalization_failed(upload_session_id: UUID, error: Exception) -> None:
    db = SessionLocal()
    try:
        upload_session = db.get(UploadSession, upload_session_id)
        if upload_session is None:
            return
        file = upload_session.file
        if file is None or file.is_deleted:
            return
        metadata = dict(file.file_metadata or {})
        metadata["finalization_error"] = _failure_message(error)
        metadata["finalization_failed_at"] = datetime.now(timezone.utc).isoformat()
        file.file_metadata = metadata
        file.status = FileStatus.FAILED
        upload_session.status = UploadSessionStatus.FAILED
        db.commit()
    finally:
        db.close()


@celery.task(name="docflow.finalize_upload")
def finalize_upload_task(upload_session_id: str) -> dict[str, str]:
    """Assemble persisted upload chunks into the File's permanent object key."""
    session_id = UUID(upload_session_id)
    db = SessionLocal()
    try:
        upload_session = db.scalar(
            select(UploadSession).where(UploadSession.id == session_id).with_for_update()
        )
        if upload_session is None:
            return {"upload_session_id": upload_session_id, "status": "not_found"}
        file = db.scalar(select(File).where(File.id == upload_session.file_id).with_for_update())
        if file is None:
            raise RuntimeError("Upload session has no target file")
        if file.is_deleted:
            return {"upload_session_id": upload_session_id, "status": "deleted"}
        if upload_session.status != UploadSessionStatus.ASSEMBLING or file.status != FileStatus.PROCESSING:
            raise RuntimeError("Upload session is not ready for finalization")

        chunks = db.scalars(
            select(FileChunk)
            .where(FileChunk.upload_session_id == upload_session.id)
            .order_by(FileChunk.chunk_index)
        ).all()
        expected_indexes = list(range(upload_session.expected_chunk_count))
        if [chunk.chunk_index for chunk in chunks] != expected_indexes:
            raise RuntimeError("Upload chunks are incomplete")

        checksum = hashlib.sha256()
        size_bytes = 0
        with SpooledTemporaryFile(max_size=10 * 1024 * 1024, mode="w+b") as assembled_file:
            for chunk in chunks:
                content = minioStorage.get_object_bytes(file.storage_bucket, chunk.storage_key)
                checksum.update(content)
                size_bytes += len(content)
                assembled_file.write(content)

            if size_bytes != upload_session.expected_size_bytes:
                raise RuntimeError("Assembled file size does not match expected size")
            assembled_file.seek(0)
            minioStorage.put_object(
                bucket_name=file.storage_bucket,
                object_name=file.storage_key,
                data=assembled_file,
                length=size_bytes,
                content_type=file.content_type,
            )

        metadata = dict(file.file_metadata or {})
        metadata["finalized_at"] = datetime.now(timezone.utc).isoformat()
        metadata.pop("finalization_error", None)
        metadata.pop("finalization_failed_at", None)
        file.file_metadata = metadata
        file.checksum_sha256 = checksum.hexdigest()
        file.size_bytes = size_bytes
        file.status = FileStatus.AVAILABLE
        upload_session.status = UploadSessionStatus.COMPLETED
        db.commit()

        # Cleanup is best-effort. A cleanup failure must not invalidate a
        # successfully finalized file.
        for chunk in chunks:
            try:
                minioStorage.remove_object(file.storage_bucket, chunk.storage_key)
            except Exception:
                pass
        return {"upload_session_id": upload_session_id, "status": "completed"}
    except Exception as error:
        db.rollback()
        _mark_finalization_failed(session_id, error)
        return {"upload_session_id": upload_session_id, "status": "failed"}
    finally:
        db.close()


@celery.task(name="docflow.cleanup_file_storage")
def cleanup_file_storage_task(file_id: str) -> dict[str, str]:
    """Best-effort cleanup for a soft-deleted file and any temporary chunks."""
    db = SessionLocal()
    try:
        file = db.get(File, UUID(file_id))
        if file is None:
            return {"file_id": file_id, "status": "not_found"}
        try:
            minioStorage.remove_object(file.storage_bucket, file.storage_key)
        except Exception:
            pass
        sessions = db.scalars(select(UploadSession).where(UploadSession.file_id == file.id)).all()
        for upload_session in sessions:
            chunks = db.scalars(select(FileChunk).where(FileChunk.upload_session_id == upload_session.id)).all()
            for chunk in chunks:
                try:
                    minioStorage.remove_object(file.storage_bucket, chunk.storage_key)
                except Exception:
                    pass
        return {"file_id": file_id, "status": "cleaned"}
    except Exception:
        return {"file_id": file_id, "status": "cleanup_failed"}
    finally:
        db.close()
