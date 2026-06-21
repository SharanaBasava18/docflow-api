import enum
import uuid

from sqlalchemy import BigInteger, Column, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from infrastructure.db.postgresql import Base


class UploadSessionStatus(str, enum.Enum):
    INITIATED = "initiated"
    UPLOADING = "uploading"
    READY_TO_COMPLETE = "ready_to_complete"
    ASSEMBLING = "assembling"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class UploadSession(Base):
    __tablename__ = "upload_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    # A File is created at initialization; an upload session never exists
    # without its target File record.
    file_id = Column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="RESTRICT"), nullable=False, index=True)
    original_filename = Column(String(512), nullable=False)
    content_type = Column(String(255), nullable=False)
    expected_size_bytes = Column(BigInteger, nullable=False)
    expected_chunk_count = Column(Integer, nullable=False)
    chunk_size_bytes = Column(Integer, nullable=False)
    status = Column(Enum(UploadSessionStatus, name="upload_session_status"), nullable=False, default=UploadSessionStatus.INITIATED)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    organization = relationship("Organization", back_populates="upload_sessions")
    created_by = relationship("User")
    file = relationship("File", back_populates="upload_sessions")
    chunks = relationship("FileChunk", back_populates="upload_session", cascade="all, delete-orphan")


class FileChunk(Base):
    __tablename__ = "file_chunks"
    __table_args__ = (UniqueConstraint("upload_session_id", "chunk_index", name="uq_file_chunks_session_index"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    upload_session_id = Column(UUID(as_uuid=True), ForeignKey("upload_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    checksum_sha256 = Column(String(64), nullable=True)
    storage_key = Column(String(1024), nullable=False)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    upload_session = relationship("UploadSession", back_populates="chunks")
