"""
File management endpoints.

GET  /files/{file_id}/url      → presigned URL (S3) or streamed bytes (local)
GET  /files/{file_id}/download → stream raw bytes with Content-Disposition
DELETE /files/{file_id}        → delete from storage + DB
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.models import UploadedFile, User
from app.services.storage.service import LocalStorageService, get_storage_service

router = APIRouter(prefix="/files", tags=["files"])


def _get_owned_file(file_id: int, user_id: int, db: Session) -> UploadedFile:
    f = db.query(UploadedFile).filter_by(id=file_id, user_id=user_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    return f


@router.get("/{file_id}/url")
def get_file_url(
    file_id: int,
    expires_in: int = 3600,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return a pre-signed URL for direct S3 download (expires in `expires_in` seconds).
    For local storage, returns a redirect to the /download endpoint instead.
    """
    f = _get_owned_file(file_id, user.id, db)
    storage = get_storage_service()

    if isinstance(storage, LocalStorageService):
        # No presigned URLs for local storage — tell the client to use /download
        return {"url": f"/api/v1/files/{file_id}/download", "expires_in": None, "storage": "local"}

    presigned = storage.generate_presigned_url(f.path, expires_in=expires_in)
    return {"url": presigned, "expires_in": expires_in, "storage": "s3"}


@router.get("/{file_id}/download")
def download_file(
    file_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Stream the raw file bytes. Works for both local and S3 storage.
    Used as fallback when pre-signed URLs aren't available (local dev).
    """
    f = _get_owned_file(file_id, user.id, db)
    storage = get_storage_service()

    try:
        content = storage.download_bytes(f.path)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not retrieve file: {exc}")

    media_type = f.content_type or "application/octet-stream"
    safe_name = Path(f.filename).name
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.delete("/{file_id}", status_code=204)
def delete_file(
    file_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Delete a file from both the storage backend (S3 or local) and the DB.
    Also nullifies any ParsedResumeData.uploaded_file_id references.
    """
    f = _get_owned_file(file_id, user.id, db)
    storage = get_storage_service()

    # Delete from storage backend
    storage.delete_file(f.path)

    # Remove DB row (cascade nullifies ParsedResumeData.uploaded_file_id via SET NULL FK)
    db.delete(f)
    db.commit()
