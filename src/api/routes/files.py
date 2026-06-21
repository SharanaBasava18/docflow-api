from uuid import UUID

from fastapi import APIRouter, Depends, File as FastAPIFile, Form, Query, UploadFile, status
from sqlalchemy.orm import Session

from auth.dependencies import get_current_user
from dto.docflow_file_dto import (
    FileListItem,
    FileMetadataResponse,
    FileStatusResponse,
    UploadChunkResponse,
    UploadCompleteRequest,
    UploadCompleteResponse,
    UploadInitializeRequest,
    UploadInitializeResponse,
)
from entities.file import FileStatus
from entities.user import User
from infrastructure.db.postgresql import get_db
from services.docflow_file_service import DocFlowFileService


router = APIRouter(prefix="/files", tags=["files"])


def get_file_service(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> DocFlowFileService:
    return DocFlowFileService(db=db, current_user=current_user)


@router.post("/upload/init", response_model=UploadInitializeResponse, status_code=status.HTTP_201_CREATED)
def initialize_upload(payload: UploadInitializeRequest, service: DocFlowFileService = Depends(get_file_service)) -> UploadInitializeResponse:
    return service.initialize_upload(payload)


@router.post("/upload/chunk", response_model=UploadChunkResponse)
async def upload_chunk(
    upload_session_id: UUID = Form(...),
    chunk_index: int = Form(...),
    chunk_file: UploadFile = FastAPIFile(...),
    service: DocFlowFileService = Depends(get_file_service),
) -> UploadChunkResponse:
    return await service.upload_chunk(upload_session_id, chunk_index, chunk_file)


@router.post("/upload/complete", response_model=UploadCompleteResponse, status_code=status.HTTP_202_ACCEPTED)
def complete_upload(payload: UploadCompleteRequest, service: DocFlowFileService = Depends(get_file_service)) -> UploadCompleteResponse:
    return service.complete_upload(payload.upload_session_id)


@router.get("", response_model=list[FileListItem])
def list_files(
    organization_id: UUID | None = None,
    file_status: FileStatus | None = Query(default=None, alias="status"),
    service: DocFlowFileService = Depends(get_file_service),
) -> list[FileListItem]:
    return service.list_files(organization_id, file_status)


@router.get("/{file_id}/status", response_model=FileStatusResponse)
def get_file_status(file_id: UUID, service: DocFlowFileService = Depends(get_file_service)) -> FileStatusResponse:
    return service.get_file_status(file_id)


@router.get("/{file_id}/metadata", response_model=FileMetadataResponse)
def get_file_metadata(file_id: UUID, service: DocFlowFileService = Depends(get_file_service)) -> FileMetadataResponse:
    return service.get_file_metadata(file_id)
