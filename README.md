# DocFlow API

DocFlow API is a multi-tenant file upload and processing backend for SaaS applications. It combines JWT authentication, organization-scoped authorization, chunked MinIO uploads, PostgreSQL metadata, and Celery finalization jobs.

## Core capabilities

- JWT registration, login, and current-user authentication.
- Organization membership authorization for every file operation.
- Resumable chunked uploads backed by persistent upload sessions.
- Asynchronous finalization: chunk assembly, SHA-256 checksum generation, and temporary-object cleanup.
- Secure time-limited MinIO download links for finalized files.
- Finalization retry and soft delete with asynchronous object cleanup.

## Local setup

1. Copy `src/.env.example` to `src/.env` and replace development secrets before any non-local deployment.
2. Start dependencies and the API:

   ```bash
   docker compose up -d --build
   ```

3. Apply the PostgreSQL schema:

   ```bash
   docker compose exec docflow-api alembic upgrade head
   ```

4. Open the OpenAPI UI at `http://localhost:8000/docs`.

## Upload lifecycle

1. Register and log in through `/auth/register` and `/auth/login`.
2. Create an organization and membership through application setup tooling (organization-management endpoints are outside this repository phase).
3. Call `POST /files/upload/init` with the organization and expected file/chunk details.
4. Upload chunks through `POST /files/upload/chunk` as multipart form data.
5. Call `POST /files/upload/complete`. The API queues finalization and returns `202 Accepted`.
6. Poll `GET /files/{file_id}/status` until the file becomes `available` or `failed`.
7. For available files, request `POST /files/{file_id}/download-link`.

## File endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/files/upload/init` | Create a File and linked upload session. |
| POST | `/files/upload/chunk` | Store one authenticated upload chunk. |
| POST | `/files/upload/complete` | Validate chunks and queue finalization. |
| GET | `/files` | List non-deleted files visible through membership. |
| GET | `/files/{file_id}/status` | Get file and upload-session progress. |
| GET | `/files/{file_id}/metadata` | Get safe file metadata. |
| POST | `/files/{file_id}/retry` | Requeue a failed finalization when valid chunks remain. |
| POST | `/files/{file_id}/download-link` | Create a time-limited download URL for an available file. |
| DELETE | `/files/{file_id}` | Soft delete a file and queue object cleanup. |

The previously shipped legacy `/api/v1/file` workflow is not mounted by the application and must not be used.
