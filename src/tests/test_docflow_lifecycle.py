from datetime import datetime, timedelta, timezone
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from dto.docflow_file_dto import UploadInitializeRequest
from entities.file import File, FileStatus
from entities.organization import OrganizationMember, OrganizationRole
from entities.upload_session import FileChunk, UploadSession, UploadSessionStatus
from entities.user import User
from main import create_application
from services.docflow_file_service import DocFlowFileService
from tasks.docflow_upload_task import finalize_upload_task


class ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeDb:
    def __init__(self, *, get_results=None, scalar_results=None, scalars_results=None):
        self.get_results = list(get_results or [])
        self.scalar_results = list(scalar_results or [])
        self.scalars_results = list(scalars_results or [])
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.flushed = 0
        self.executed = 0

    def get(self, model, entity_id):
        if self.get_results:
            return self.get_results.pop(0)
        return None

    def scalar(self, statement):
        if self.scalar_results:
            return self.scalar_results.pop(0)
        return None

    def scalars(self, statement):
        if self.scalars_results:
            return ScalarRows(self.scalars_results.pop(0))
        return ScalarRows([])

    def add(self, instance):
        self.added.append(instance)

    def add_all(self, instances):
        self.added.extend(instances)

    def execute(self, statement, params=None):
        self.executed += 1

    def flush(self):
        self.flushed += 1

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class MemoryStorage:
    def __init__(self, objects=None, fail_on_read=False):
        self.objects = dict(objects or {})
        self.removed = []
        self.fail_on_read = fail_on_read

    def put_bytes(self, bucket_name, object_name, content, content_type):
        self.objects[(bucket_name, object_name)] = content

    def get_object_bytes(self, bucket_name, object_name):
        if self.fail_on_read:
            raise RuntimeError("chunk read failed")
        return self.objects[(bucket_name, object_name)]

    def put_object(self, bucket_name, object_name, data, length, content_type):
        self.objects[(bucket_name, object_name)] = data.read()

    def remove_object(self, bucket_name, object_name):
        self.removed.append((bucket_name, object_name))
        self.objects.pop((bucket_name, object_name), None)

    def get_presigned_url(self, *args, **kwargs):
        return "https://storage.example/signed"


class AsyncUpload:
    content_type = "application/octet-stream"

    def __init__(self, content):
        self.content = content

    async def read(self):
        return self.content


def make_user():
    return User(id=uuid4(), email="owner@example.com", full_name="Owner", hashed_password="hashed")


def make_membership(user, organization_id):
    return OrganizationMember(organization_id=organization_id, user_id=user.id, role=OrganizationRole.OWNER)


def make_file(user, organization_id, *, status=FileStatus.PENDING, is_deleted=False):
    file_id = uuid4()
    return File(
        id=file_id,
        organization_id=organization_id,
        owner_user_id=user.id,
        original_filename="report.pdf",
        stored_filename=f"{file_id.hex}_report.pdf",
        content_type="application/pdf",
        size_bytes=10,
        storage_bucket="private",
        storage_key=f"organizations/{organization_id}/files/{file_id}/{file_id.hex}_report.pdf",
        status=status,
        file_metadata={},
        is_deleted=is_deleted,
    )


def make_session(user, file, *, status=UploadSessionStatus.INITIATED, expected_size=10):
    upload_session = UploadSession(
        id=uuid4(),
        organization_id=file.organization_id,
        created_by_user_id=user.id,
        file_id=file.id,
        original_filename=file.original_filename,
        content_type=file.content_type,
        expected_size_bytes=expected_size,
        expected_chunk_count=2,
        chunk_size_bytes=5,
        status=status,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    upload_session.file = file
    return upload_session


def make_chunks(upload_session):
    return [
        FileChunk(upload_session_id=upload_session.id, chunk_index=0, size_bytes=5, storage_key="chunk-0"),
        FileChunk(upload_session_id=upload_session.id, chunk_index=1, size_bytes=5, storage_key="chunk-1"),
    ]


def make_service(*, file_status=FileStatus.FAILED, session_status=UploadSessionStatus.FAILED, chunks=None):
    user = make_user()
    organization_id = uuid4()
    file = make_file(user, organization_id, status=file_status)
    upload_session = make_session(user, file, status=session_status)
    membership = make_membership(user, organization_id)
    db = FakeDb(
        get_results=[file],
        scalar_results=[membership, upload_session],
        scalars_results=[chunks if chunks is not None else make_chunks(upload_session)],
    )
    return DocFlowFileService(db=db, current_user=user), user, file, upload_session, db


def test_unauthorized_file_routes_are_rejected():
    app = create_application()

    with TestClient(app) as client:
        response = client.get("/files")

    assert response.status_code == 401


def test_membership_is_required_for_file_access():
    user = make_user()
    file = make_file(user, uuid4())
    service = DocFlowFileService(db=FakeDb(get_results=[file], scalar_results=[None]), current_user=user)

    with pytest.raises(HTTPException) as exc_info:
        service.get_file_metadata(file.id)

    assert exc_info.value.status_code == 403


def test_upload_init_creates_file_and_session():
    user = make_user()
    organization_id = uuid4()
    db = FakeDb(scalar_results=[make_membership(user, organization_id)])
    service = DocFlowFileService(db=db, current_user=user)

    response = service.initialize_upload(
        UploadInitializeRequest(
            organization_id=organization_id,
            original_filename="../report.pdf",
            content_type="application/pdf",
            expected_size_bytes=10,
            expected_chunk_count=2,
            chunk_size_bytes=5,
        )
    )

    assert response.status == UploadSessionStatus.INITIATED
    assert response.chunk_size_bytes == 5
    assert len(db.added) == 2
    assert db.added[0].original_filename == "report.pdf"
    assert db.added[1].file_id == db.added[0].id
    assert db.commits == 1


def test_upload_chunk_stores_chunk_and_updates_progress(monkeypatch):
    user = make_user()
    organization_id = uuid4()
    file = make_file(user, organization_id)
    upload_session = make_session(user, file)
    storage = MemoryStorage()
    db = FakeDb(
        get_results=[upload_session],
        scalar_results=[make_membership(user, organization_id), None, 1],
    )
    service = DocFlowFileService(db=db, current_user=user)
    monkeypatch.setattr("services.docflow_file_service.minioStorage", storage)

    response = asyncio.run(service.upload_chunk(upload_session.id, 0, AsyncUpload(b"hello")))

    assert response.status == UploadSessionStatus.UPLOADING
    assert response.uploaded_chunk_count == 1
    assert db.executed == 1
    assert db.flushed == 1
    assert db.commits == 1
    assert db.added[0].storage_key.endswith("/chunks/0")


def test_upload_complete_rejects_missing_chunks():
    service, _, file, upload_session, _ = make_service(
        file_status=FileStatus.PENDING,
        session_status=UploadSessionStatus.READY_TO_COMPLETE,
        chunks=[FileChunk(upload_session_id=uuid4(), chunk_index=0, size_bytes=5, storage_key="chunk-0")],
    )
    upload_session.file = file

    with pytest.raises(HTTPException) as exc_info:
        service.complete_upload(upload_session.id)

    assert exc_info.value.status_code == 409


def test_upload_complete_queues_finalization(monkeypatch):
    service, _, file, upload_session, db = make_service(
        file_status=FileStatus.PENDING,
        session_status=UploadSessionStatus.READY_TO_COMPLETE,
    )
    upload_session.file = file
    monkeypatch.setattr(
        "services.docflow_file_service.finalize_upload_task",
        SimpleNamespace(delay=lambda upload_session_id: SimpleNamespace(id=f"task-{upload_session_id}")),
    )

    response = service.complete_upload(upload_session.id)

    assert response.file_status == FileStatus.PROCESSING
    assert response.upload_status == UploadSessionStatus.ASSEMBLING
    assert file.processing_task_id == f"task-{upload_session.id}"
    assert db.commits == 2


def test_retry_finalization_requeues_failed_file(monkeypatch):
    service, _, file, upload_session, db = make_service()
    monkeypatch.setattr(
        "services.docflow_file_service.finalize_upload_task",
        SimpleNamespace(delay=lambda upload_session_id: SimpleNamespace(id=f"task-{upload_session_id}")),
    )

    response = service.retry_finalization(file.id)

    assert response.file_id == file.id
    assert response.upload_session_id == upload_session.id
    assert response.file_status == FileStatus.PROCESSING
    assert response.upload_status == UploadSessionStatus.ASSEMBLING
    assert file.status == FileStatus.PROCESSING
    assert upload_session.status == UploadSessionStatus.ASSEMBLING
    assert db.commits == 2


def test_retry_finalization_rejects_non_failed_file():
    service, _, file, _, _ = make_service(file_status=FileStatus.AVAILABLE, session_status=UploadSessionStatus.COMPLETED)

    with pytest.raises(HTTPException) as exc_info:
        service.retry_finalization(file.id)

    assert exc_info.value.status_code == 409


def test_download_link_requires_available_file(monkeypatch):
    service, _, file, _, _ = make_service(file_status=FileStatus.AVAILABLE, session_status=UploadSessionStatus.COMPLETED)
    monkeypatch.setattr("services.docflow_file_service.minioStorage", MemoryStorage())

    response = service.create_download_link(file.id)

    assert response.file_id == file.id
    assert response.url == "https://storage.example/signed"
    assert response.expires_at > datetime.now(timezone.utc)


def test_download_link_rejects_unavailable_file():
    service, _, file, _, _ = make_service(file_status=FileStatus.PROCESSING, session_status=UploadSessionStatus.ASSEMBLING)

    with pytest.raises(HTTPException) as exc_info:
        service.create_download_link(file.id)

    assert exc_info.value.status_code == 409


def test_soft_delete_marks_file_cancels_session_and_excludes_from_listing(monkeypatch):
    service, user, file, upload_session, db = make_service(
        file_status=FileStatus.PROCESSING,
        session_status=UploadSessionStatus.ASSEMBLING,
    )
    db.scalar_results = [file, make_membership(user, file.organization_id)]
    db.scalars_results = [[upload_session]]
    monkeypatch.setattr(
        "services.docflow_file_service.cleanup_file_storage_task",
        SimpleNamespace(delay=lambda file_id: SimpleNamespace(id=f"cleanup-{file_id}")),
    )

    response = service.soft_delete_file(file.id)

    assert response.status == FileStatus.DELETED
    assert response.is_deleted is True
    assert file.is_deleted is True
    assert upload_session.status == UploadSessionStatus.CANCELLED

    list_db = FakeDb(scalars_results=[[]])
    listed = DocFlowFileService(db=list_db, current_user=user).list_files(None, None)
    assert listed == []


def test_deleted_file_metadata_download_and_retry_are_rejected():
    for action in ("metadata", "download", "retry"):
        service, _, file, _, _ = make_service(file_status=FileStatus.DELETED)
        file.is_deleted = True
        with pytest.raises(HTTPException) as exc_info:
            if action == "metadata":
                service.get_file_metadata(file.id)
            elif action == "download":
                service.create_download_link(file.id)
            else:
                service.retry_finalization(file.id)
        assert exc_info.value.status_code == 410


def test_finalization_success_updates_file_session_and_cleans_chunks(monkeypatch):
    user = make_user()
    file = make_file(user, uuid4(), status=FileStatus.PROCESSING)
    upload_session = make_session(user, file, status=UploadSessionStatus.ASSEMBLING)
    chunks = make_chunks(upload_session)
    storage = MemoryStorage({
        (file.storage_bucket, "chunk-0"): b"hello",
        (file.storage_bucket, "chunk-1"): b"world",
    })
    db = FakeDb(scalar_results=[upload_session, file], scalars_results=[chunks])
    monkeypatch.setattr("tasks.docflow_upload_task.SessionLocal", lambda: db)
    monkeypatch.setattr("tasks.docflow_upload_task.minioStorage", storage)

    result = finalize_upload_task(str(upload_session.id))

    assert result["status"] == "completed"
    assert file.status == FileStatus.AVAILABLE
    assert upload_session.status == UploadSessionStatus.COMPLETED
    assert file.checksum_sha256 == "936a185caaa266bb9cbe981e9e05cb78cd732b0b3280eb944412bb6f8f8f07af"
    assert storage.objects[(file.storage_bucket, file.storage_key)] == b"helloworld"
    assert (file.storage_bucket, "chunk-0") in storage.removed
    assert db.commits == 1


def test_finalization_failure_marks_file_and_session_failed(monkeypatch):
    user = make_user()
    file = make_file(user, uuid4(), status=FileStatus.PROCESSING)
    upload_session = make_session(user, file, status=UploadSessionStatus.ASSEMBLING)
    chunks = make_chunks(upload_session)
    worker_db = FakeDb(scalar_results=[upload_session, file], scalars_results=[chunks])
    failure_db = FakeDb(get_results=[upload_session])
    monkeypatch.setattr("tasks.docflow_upload_task.SessionLocal", lambda: worker_db if worker_db.commits == 0 and worker_db.rollbacks == 0 else failure_db)
    monkeypatch.setattr("tasks.docflow_upload_task.minioStorage", MemoryStorage(fail_on_read=True))

    result = finalize_upload_task(str(upload_session.id))

    assert result["status"] == "failed"
    assert file.status == FileStatus.FAILED
    assert upload_session.status == UploadSessionStatus.FAILED
    assert "finalization_error" in file.file_metadata
