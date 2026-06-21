from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.config import config
from dto.docflow_file_dto import (
    FileListItem,
    FileMetadataResponse,
    FileStatusResponse,
    DeleteResponse,
    DownloadLinkResponse,
    RetryResponse,
    UploadChunkResponse,
    UploadCompleteResponse,
    UploadInitializeRequest,
    UploadInitializeResponse,
    UploadSessionProgress,
)
from entities.file import File, FileStatus
from entities.organization import OrganizationMember
from entities.upload_session import FileChunk, UploadSession, UploadSessionStatus
from entities.user import User
from infrastructure.minio import minioStorage
from tasks.docflow_upload_task import cleanup_file_storage_task, finalize_upload_task


class DocFlowFileService:
    def __init__(self, db: Session, current_user: User) -> None:
        self.db = db
        self.current_user = current_user

    def _membership_for(self, organization_id: UUID) -> OrganizationMember:
        membership = self.db.scalar(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == self.current_user.id,
            )
        )
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization membership is required")
        return membership

    def _file_for_access(self, file_id: UUID) -> File:
        file = self.db.get(File, file_id)
        if file is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
        self._membership_for(file.organization_id)
        return file

    def _session_for_access(self, upload_session_id: UUID) -> UploadSession:
        session = self.db.get(UploadSession, upload_session_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload session not found")
        file = session.file
        if file is None or file.organization_id != session.organization_id:
            # This is a data-integrity violation, not an authorization choice.
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload session is not linked to a valid file")
        if file.is_deleted:
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="File has been deleted")
        self._membership_for(file.organization_id)
        return session

    def _enqueue_finalization(self, file: File, upload_session: UploadSession) -> str:
        try:
            task = finalize_upload_task.delay(str(upload_session.id))
        except Exception as error:
            metadata = dict(file.file_metadata or {})
            metadata["finalization_error"] = f"Unable to queue finalization: {str(error)[:500]}"
            file.file_metadata = metadata
            file.status = FileStatus.FAILED
            upload_session.status = UploadSessionStatus.FAILED
            self.db.commit()
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Unable to queue file finalization")
        file.processing_task_id = task.id
        self.db.commit()
        return task.id

    @staticmethod
    def _safe_filename(filename: str) -> str:
        name = PurePath(filename).name.strip()
        if not name or name in {".", ".."}:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid filename")
        return name

    def initialize_upload(self, payload: UploadInitializeRequest) -> UploadInitializeResponse:
        self._membership_for(payload.organization_id)
        if payload.chunk_size_bytes > config.APP_MAX_CHUNK_SIZE:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Chunk size exceeds configured limit")

        file_id = uuid4()
        upload_session_id = uuid4()
        filename = self._safe_filename(payload.original_filename)
        stored_filename = f"{file_id.hex}_{filename}"
        storage_key = f"organizations/{payload.organization_id}/files/{file_id}/{stored_filename}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=config.UPLOAD_SESSION_EXPIRE_MINUTES)

        file = File(
            id=file_id,
            organization_id=payload.organization_id,
            owner_user_id=self.current_user.id,
            original_filename=filename,
            stored_filename=stored_filename,
            content_type=payload.content_type,
            size_bytes=payload.expected_size_bytes,
            storage_bucket=config.MINIO_PRIVATE_BUCKET,
            storage_key=storage_key,
            status=FileStatus.PENDING,
            file_metadata={},
        )
        upload_session = UploadSession(
            id=upload_session_id,
            organization_id=payload.organization_id,
            created_by_user_id=self.current_user.id,
            file_id=file_id,
            original_filename=filename,
            content_type=payload.content_type,
            expected_size_bytes=payload.expected_size_bytes,
            expected_chunk_count=payload.expected_chunk_count,
            chunk_size_bytes=payload.chunk_size_bytes,
            status=UploadSessionStatus.INITIATED,
            expires_at=expires_at,
        )
        self.db.add_all([file, upload_session])
        self.db.commit()
        return UploadInitializeResponse(
            file_id=file_id,
            upload_session_id=upload_session_id,
            status=upload_session.status,
            chunk_size_bytes=payload.chunk_size_bytes,
            expires_at=expires_at,
        )

    async def upload_chunk(self, upload_session_id: UUID, chunk_index: int, upload_file: UploadFile) -> UploadChunkResponse:
        upload_session = self._session_for_access(upload_session_id)
        now = datetime.now(timezone.utc)
        if upload_session.expires_at <= now:
            upload_session.status = UploadSessionStatus.EXPIRED
            self.db.commit()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload session has expired")
        if upload_session.status not in {UploadSessionStatus.INITIATED, UploadSessionStatus.UPLOADING}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload session is not accepting chunks")
        if chunk_index < 0 or chunk_index >= upload_session.expected_chunk_count:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Chunk index is out of range")
        content = await upload_file.read()
        if not content:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Chunk must not be empty")
        if len(content) > upload_session.chunk_size_bytes or len(content) > config.APP_MAX_CHUNK_SIZE:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Chunk size exceeds allowed limit")

        storage_key = f"organizations/{upload_session.organization_id}/uploads/{upload_session.id}/chunks/{chunk_index}"
        try:
            # The database uniqueness constraint remains authoritative. This
            # PostgreSQL transaction lock also prevents a concurrent duplicate
            # request from overwriting the deterministic MinIO chunk key.
            self.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": f"{upload_session.id}:{chunk_index}"},
            )
            if self.db.scalar(
                select(FileChunk.id).where(
                    FileChunk.upload_session_id == upload_session.id,
                    FileChunk.chunk_index == chunk_index,
                )
            ):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Chunk index has already been uploaded")

            minioStorage.put_bytes(
                bucket_name=config.MINIO_PRIVATE_BUCKET,
                object_name=storage_key,
                content=content,
                content_type=upload_file.content_type or "application/octet-stream",
            )
            self.db.add(FileChunk(
                upload_session_id=upload_session.id,
                chunk_index=chunk_index,
                size_bytes=len(content),
                storage_key=storage_key,
            ))
            self.db.flush()
            uploaded_count = self.db.scalar(
                select(func.count(FileChunk.id)).where(FileChunk.upload_session_id == upload_session.id)
            ) or 0
            upload_session.status = (
                UploadSessionStatus.READY_TO_COMPLETE
                if uploaded_count == upload_session.expected_chunk_count
                else UploadSessionStatus.UPLOADING
            )
            self.db.commit()
        except HTTPException:
            self.db.rollback()
            raise
        except IntegrityError:
            self.db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Chunk index has already been uploaded")
        except Exception:
            self.db.rollback()
            raise

        return UploadChunkResponse(
            upload_session_id=upload_session.id,
            chunk_index=chunk_index,
            status=upload_session.status,
            uploaded_chunk_count=uploaded_count,
            expected_chunk_count=upload_session.expected_chunk_count,
        )

    def complete_upload(self, upload_session_id: UUID) -> UploadCompleteResponse:
        upload_session = self._session_for_access(upload_session_id)
        if upload_session.expires_at <= datetime.now(timezone.utc):
            upload_session.status = UploadSessionStatus.EXPIRED
            self.db.commit()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload session has expired")
        if upload_session.status == UploadSessionStatus.COMPLETED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload session is already completed")
        if upload_session.status != UploadSessionStatus.READY_TO_COMPLETE:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="All chunks must be uploaded before completion")

        chunks = self.db.scalars(
            select(FileChunk).where(FileChunk.upload_session_id == upload_session.id).order_by(FileChunk.chunk_index)
        ).all()
        expected_indexes = list(range(upload_session.expected_chunk_count))
        if [chunk.chunk_index for chunk in chunks] != expected_indexes:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload chunks are incomplete")
        if sum(chunk.size_bytes for chunk in chunks) != upload_session.expected_size_bytes:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Uploaded chunk size does not match expected file size")

        file = upload_session.file
        upload_session.status = UploadSessionStatus.ASSEMBLING
        file.status = FileStatus.PROCESSING
        self.db.commit()
        self._enqueue_finalization(file, upload_session)
        return UploadCompleteResponse(
            file_id=file.id,
            upload_session_id=upload_session.id,
            file_status=file.status,
            upload_status=upload_session.status,
        )

    def get_file_status(self, file_id: UUID) -> FileStatusResponse:
        file = self._file_for_access(file_id)
        upload_session = self.db.scalar(
            select(UploadSession)
            .where(UploadSession.file_id == file.id)
            .order_by(UploadSession.created_at.desc())
        )
        progress = None
        if upload_session:
            uploaded_count = self.db.scalar(
                select(func.count(FileChunk.id)).where(FileChunk.upload_session_id == upload_session.id)
            )
            progress = UploadSessionProgress(
                id=upload_session.id,
                status=upload_session.status,
                uploaded_chunk_count=uploaded_count,
                expected_chunk_count=upload_session.expected_chunk_count,
                expires_at=upload_session.expires_at,
            )
        return FileStatusResponse(id=file.id, status=file.status, is_deleted=file.is_deleted, upload_session=progress)

    def list_files(self, organization_id: UUID | None, file_status: FileStatus | None) -> list[FileListItem]:
        statement = (
            select(File)
            .join(OrganizationMember, OrganizationMember.organization_id == File.organization_id)
            .where(
                OrganizationMember.user_id == self.current_user.id,
                File.is_deleted.is_(False),
                File.status != FileStatus.DELETED,
            )
            .order_by(File.created_at.desc())
        )
        if organization_id is not None:
            self._membership_for(organization_id)
            statement = statement.where(File.organization_id == organization_id)
        if file_status is not None:
            statement = statement.where(File.status == file_status)
        files = self.db.scalars(statement).all()
        return [
            FileListItem(
                id=file.id,
                organization_id=file.organization_id,
                owner_user_id=file.owner_user_id,
                original_filename=file.original_filename,
                content_type=file.content_type,
                size_bytes=file.size_bytes,
                status=file.status,
                created_at=file.created_at,
            )
            for file in files
        ]

    def get_file_metadata(self, file_id: UUID) -> FileMetadataResponse:
        file = self._file_for_access(file_id)
        return FileMetadataResponse.model_validate(file)

    def retry_finalization(self, file_id: UUID) -> RetryResponse:
        file = self._file_for_access(file_id)
        upload_session = self.db.scalar(
            select(UploadSession)
            .where(UploadSession.file_id == file.id)
            .order_by(UploadSession.created_at.desc())
        )
        if upload_session is None or file.status != FileStatus.FAILED or upload_session.status != UploadSessionStatus.FAILED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="File finalization is not retryable")

        chunks = self.db.scalars(
            select(FileChunk).where(FileChunk.upload_session_id == upload_session.id).order_by(FileChunk.chunk_index)
        ).all()
        if (
            [chunk.chunk_index for chunk in chunks] != list(range(upload_session.expected_chunk_count))
            or sum(chunk.size_bytes for chunk in chunks) != upload_session.expected_size_bytes
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Uploaded chunks are not valid for retry")

        upload_session.status = UploadSessionStatus.ASSEMBLING
        file.status = FileStatus.PROCESSING
        self.db.commit()
        task_id = self._enqueue_finalization(file, upload_session)
        return RetryResponse(
            file_id=file.id,
            upload_session_id=upload_session.id,
            file_status=file.status,
            upload_status=upload_session.status,
            processing_task_id=task_id,
        )

    def soft_delete_file(self, file_id: UUID) -> DeleteResponse:
        file = self.db.scalar(select(File).where(File.id == file_id).with_for_update())
        if file is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
        self._membership_for(file.organization_id)
        if not file.is_deleted:
            file.is_deleted = True
            file.status = FileStatus.DELETED
            active_sessions = self.db.scalars(select(UploadSession).where(UploadSession.file_id == file.id)).all()
            for upload_session in active_sessions:
                if upload_session.status in {
                    UploadSessionStatus.INITIATED,
                    UploadSessionStatus.UPLOADING,
                    UploadSessionStatus.READY_TO_COMPLETE,
                    UploadSessionStatus.ASSEMBLING,
                }:
                    upload_session.status = UploadSessionStatus.CANCELLED
            self.db.commit()
            try:
                cleanup_file_storage_task.delay(str(file.id))
            except Exception:
                # The database deletion is authoritative; cleanup is retriable
                # and must not turn a successful soft delete into a failure.
                pass
        return DeleteResponse(file_id=file.id, status=file.status, is_deleted=file.is_deleted)

    def create_download_link(self, file_id: UUID) -> DownloadLinkResponse:
        file = self._file_for_access(file_id)
        if file.is_deleted or file.status != FileStatus.AVAILABLE:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="File is not available for download")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=config.DOWNLOAD_LINK_EXPIRE_SECONDS)
        url = minioStorage.get_presigned_url(
            "GET",
            bucket_name=file.storage_bucket,
            object_name=file.storage_key,
            expires=timedelta(seconds=config.DOWNLOAD_LINK_EXPIRE_SECONDS),
        )
        return DownloadLinkResponse(file_id=file.id, url=url, expires_at=expires_at)
