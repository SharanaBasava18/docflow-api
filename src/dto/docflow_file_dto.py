from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from entities.file import FileStatus
from entities.upload_session import UploadSessionStatus


class UploadInitializeRequest(BaseModel):
    organization_id: UUID
    original_filename: str = Field(min_length=1, max_length=512)
    content_type: str = Field(min_length=1, max_length=255)
    expected_size_bytes: int = Field(gt=0)
    expected_chunk_count: int = Field(gt=0)
    chunk_size_bytes: int = Field(gt=0)


class UploadInitializeResponse(BaseModel):
    file_id: UUID
    upload_session_id: UUID
    status: UploadSessionStatus
    chunk_size_bytes: int
    expires_at: datetime


class UploadChunkResponse(BaseModel):
    upload_session_id: UUID
    chunk_index: int
    status: UploadSessionStatus
    uploaded_chunk_count: int
    expected_chunk_count: int


class UploadCompleteRequest(BaseModel):
    upload_session_id: UUID


class UploadCompleteResponse(BaseModel):
    file_id: UUID
    upload_session_id: UUID
    file_status: FileStatus
    upload_status: UploadSessionStatus


class UploadSessionProgress(BaseModel):
    id: UUID
    status: UploadSessionStatus
    uploaded_chunk_count: int
    expected_chunk_count: int
    expires_at: datetime


class FileStatusResponse(BaseModel):
    id: UUID
    status: FileStatus
    is_deleted: bool
    upload_session: UploadSessionProgress | None


class FileListItem(BaseModel):
    id: UUID
    organization_id: UUID
    owner_user_id: UUID
    original_filename: str
    content_type: str
    size_bytes: int
    status: FileStatus
    created_at: datetime


class FileMetadataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    owner_user_id: UUID
    original_filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str | None
    status: FileStatus
    file_metadata: dict
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
