import enum
import uuid

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from infrastructure.db.postgresql import Base


class FileStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    AVAILABLE = "available"
    FAILED = "failed"
    DELETED = "deleted"


class File(Base):
    __tablename__ = "files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True)
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    original_filename = Column(String(512), nullable=False)
    stored_filename = Column(String(512), nullable=False)
    content_type = Column(String(255), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    storage_bucket = Column(String(255), nullable=False)
    storage_key = Column(String(1024), nullable=False)
    checksum_sha256 = Column(String(64), nullable=True)
    status = Column(Enum(FileStatus, name="file_status"), nullable=False, default=FileStatus.PENDING)
    # `metadata` is reserved by SQLAlchemy's declarative base. Use an explicit
    # domain name in Python and PostgreSQL to avoid mapper collisions.
    file_metadata = Column(JSONB, nullable=False, default=dict)
    processing_task_id = Column(String(255), nullable=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    organization = relationship("Organization", back_populates="files")
    owner = relationship("User", back_populates="owned_files", foreign_keys=[owner_user_id])
    upload_sessions = relationship("UploadSession", back_populates="file")
    access_logs = relationship("FileAccessLog", back_populates="file")
