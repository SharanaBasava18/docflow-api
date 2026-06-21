# DocFlow API

DocFlow API is a multi-tenant file upload and processing backend for SaaS applications. It provides JWT authentication, organization-scoped file access, chunked uploads to MinIO, PostgreSQL metadata, and Celery finalization jobs.

## Stack

- FastAPI for the HTTP API
- PostgreSQL, SQLAlchemy, and Alembic for persistence and migrations
- JWT bearer authentication
- MinIO for private object storage
- RabbitMQ and Celery for async finalization and cleanup
- Pytest for backend tests

## Current Scope

DocFlow includes user registration/login/current-user endpoints, organization membership checks, file metadata records, upload sessions, chunk records, access-log schema, chunk upload, async finalization, retry, soft delete, and secure download-link generation.

Organization-management endpoints are intentionally out of scope for this phase. Organizations and memberships can be created through setup scripts, direct database seeding, or future application tooling.

## Local Setup

1. Copy `src/.env.example` to `src/.env`.
2. Replace `JWT_SECRET_KEY` before any non-local deployment.
3. Start the API and dependencies:

   ```bash
   docker compose up -d --build
   ```

   The `docflow-api` container runs both Uvicorn and a Celery worker through `supervisord`.

4. Apply the database schema:

   ```bash
   docker compose exec docflow-api alembic upgrade head
   ```

5. Open the API docs at `http://localhost:8000/docs`.

MinIO API is exposed locally on `http://localhost:9001`; the MinIO console is exposed on `http://localhost:9090`.

## Auth And Access Control

All `/files` endpoints require a JWT bearer token. File operations are authorized through `OrganizationMember`: a user can only initialize uploads, list files, inspect metadata/status, retry, delete, or create download links for organizations they belong to.

Chunk upload authorization is derived from the upload session and linked file organization. The client does not provide a trusted organization id during chunk upload.

## Upload Workflow

1. Register with `POST /auth/register`.
2. Log in with `POST /auth/login` and use the returned access token.
3. Create or seed an organization membership for the user.
4. Start an upload with `POST /files/upload/init`.
5. Upload each chunk with `POST /files/upload/chunk` as multipart form data:
   - `upload_session_id`
   - `chunk_index`
   - `chunk_file`
6. Complete the upload with `POST /files/upload/complete`.
7. Poll `GET /files/{file_id}/status` until the file is `available` or `failed`.

Completion validates that all expected chunk indexes exist and that the aggregate uploaded size matches the initialized expected size before finalization is queued.

## Finalization Lifecycle

After completion, the API marks the upload session `assembling` and the file `processing`, then queues `docflow.finalize_upload`.

The worker:
- loads the upload session and file
- reads chunk objects from MinIO in chunk order
- assembles the permanent object under the file storage key
- computes and stores the SHA-256 checksum
- marks the file `available` and session `completed`
- removes temporary chunks on a best-effort basis

If finalization fails, the worker marks the file and upload session `failed` and stores failure details in `file_metadata`.

## Retry, Delete, And Download Links

- `POST /files/{file_id}/retry` requeues finalization only when both the file and latest upload session are failed and the chunk set is still valid.
- `DELETE /files/{file_id}` soft deletes the file, marks active upload sessions cancelled, excludes the file from normal listings, and queues best-effort object cleanup.
- `POST /files/{file_id}/download-link` returns a time-limited MinIO presigned URL only for available, non-deleted files.

Deleted files are rejected for metadata, retry, and download-link flows. The status endpoint remains callable so clients can see that a known file has been deleted.

## Tests

Install dependencies inside the API environment, then run:

```bash
cd src
python -m pytest -q
```

The current test suite focuses on the active DocFlow API and mocked service/task behavior, so it can run without live MinIO, RabbitMQ, or PostgreSQL services once Python dependencies are installed:
- unauthorized file access
- organization membership enforcement
- upload init/chunk/complete validation
- retry lifecycle rules
- soft delete lifecycle rules
- secure download-link eligibility
- finalization success and failure paths

## Environment

Important settings in `src/.env.example`:

- `DATABASE_URL` and `TEST_DATABASE_URL`
- `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`
- `MINIO_ENDPOINT`, `MINIO_URL`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`
- `MINIO_PRIVATE_BUCKET`
- `RABBITMQ_*`
- `APP_MAX_CHUNK_SIZE`
- `UPLOAD_SESSION_EXPIRE_MINUTES`
- `DOWNLOAD_LINK_EXPIRE_SECONDS`

## Notes

The old template upload API has been removed from the active source tree. The supported workflow is the DocFlow `/files` API described above.
