import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import relationship

from infrastructure.db.postgresql import Base


class FileAccessLog(Base):
    __tablename__ = "file_access_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id = Column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(100), nullable=False)
    ip_address = Column(INET, nullable=True)
    user_agent = Column(String(1024), nullable=True)
    extra_metadata = Column(JSONB, nullable=False, default=dict)
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    file = relationship("File", back_populates="access_logs")
    organization = relationship("Organization")
    actor = relationship("User")
