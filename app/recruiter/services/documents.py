"""
Secure candidate document storage (CVs, cover letters).

Replaces the old `_persist_source_file`, which wrote raw bytes to
`./storage/uploads/recruiter/{agency}/{client-supplied filename}` with
`open()`. That bypassed the StorageService (so S3/MinIO/Blob were never used),
let two candidates with the same filename overwrite each other, accepted
`../` in the filename (path traversal), swallowed write errors, and had no
download route.

Now:
  * every upload is validated (size, extension, magic bytes) and given a
    sanitised filename;
  * bytes go through the shared StorageService under
    `recruiter/{agency_id}/candidates/{candidate_id}/` with a uuid key prefix;
  * a CandidateDocument row is the only pointer, always agency-scoped;
  * deletes remove the stored object as well as the row (GDPR erasure).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.recruiter.models import CandidateDocument
from app.services.storage.service import get_storage_service

logger = logging.getLogger(__name__)

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024  # 10 MB

# extension → (content type, magic-byte check)
_PDF = b"%PDF-"
_ZIP = b"PK\x03\x04"           # .docx (OOXML is a zip container)
_OLE = b"\xd0\xcf\x11\xe0"     # legacy .doc

ALLOWED_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".txt": "text/plain",
}


class DocumentRejected(ValueError):
    """Upload failed validation. The message is safe to show to the caller."""


@dataclass
class ValidatedFile:
    filename: str
    content_type: str
    content: bytes
    sha256: str


def secure_filename(name: str | None, default: str = "document") -> str:
    """Strip any path, control chars and odd characters; keep a short safe name."""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    if not name:
        name = default
    stem, ext = os.path.splitext(name)
    return f"{stem[:80]}{ext[:10].lower()}"


def validate_upload(filename: str | None, content: bytes, *, max_bytes: int = MAX_DOCUMENT_BYTES) -> ValidatedFile:
    if not content:
        raise DocumentRejected("The uploaded file is empty.")
    if len(content) > max_bytes:
        raise DocumentRejected(f"File is too large (max {max_bytes // (1024 * 1024)} MB).")
    safe = secure_filename(filename, default="cv.txt")
    ext = os.path.splitext(safe)[1].lower()
    if ext not in ALLOWED_TYPES:
        raise DocumentRejected("Unsupported file type. Upload a PDF, DOCX, DOC or TXT file.")
    head = content[:8]
    ok = (
        (ext == ".pdf" and head.startswith(_PDF))
        or (ext == ".docx" and head.startswith(_ZIP))
        or (ext == ".doc" and head.startswith(_OLE))
        or (ext == ".txt" and _looks_like_text(content))
    )
    if not ok:
        raise DocumentRejected("File contents do not match its extension.")
    return ValidatedFile(
        filename=safe,
        content_type=ALLOWED_TYPES[ext],
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _looks_like_text(content: bytes) -> bool:
    sample = content[:4096]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        try:
            sample.decode("latin-1")
        except UnicodeDecodeError:  # pragma: no cover - latin-1 never fails
            return False
    return True


def store_document(
    db: Session,
    *,
    agency_id: int,
    candidate_id: int,
    file: ValidatedFile,
    kind: str = "cv",
    uploaded_via: str = "bulk_cv",
    application_id: int | None = None,
) -> CandidateDocument:
    """Upload bytes through the StorageService and record the pointer. Caller commits.

    Raises on storage failure — unlike the old best-effort writer, a CV that
    could not be stored is an error the caller must surface.
    """
    storage = get_storage_service()
    uri = storage.upload_bytes(
        content=file.content,
        key_prefix=f"recruiter/{int(agency_id)}/candidates/{int(candidate_id)}",
        filename=file.filename,
        content_type=file.content_type,
    )
    doc = CandidateDocument(
        agency_id=agency_id,
        candidate_id=candidate_id,
        application_id=application_id,
        kind=kind,
        storage_uri=uri,
        original_filename=file.filename,
        content_type=file.content_type,
        size_bytes=len(file.content),
        sha256=file.sha256,
        uploaded_via=uploaded_via,
    )
    db.add(doc)
    db.flush()
    return doc


def get_document(db: Session, agency_id: int, document_id: int) -> CandidateDocument | None:
    doc = db.get(CandidateDocument, document_id)
    if doc is None or doc.agency_id != agency_id:
        return None
    return doc


def list_documents(
    db: Session, agency_id: int, candidate_id: int, application_id: int | None = None
) -> list[CandidateDocument]:
    q = db.query(CandidateDocument).filter(
        CandidateDocument.agency_id == agency_id,
        CandidateDocument.candidate_id == candidate_id,
    )
    if application_id is not None:
        q = q.filter(CandidateDocument.application_id == application_id)
    return q.order_by(CandidateDocument.id.desc()).all()


def read_document(doc: CandidateDocument) -> bytes:
    return get_storage_service().download_bytes(doc.storage_uri)


class DocumentDeleteError(RuntimeError):
    """A stored object could not be removed — erasure must not report success."""


def _strict_delete(storage, uri: str) -> None:
    """Delete an object and RAISE on failure (the shared service swallows errors)."""
    from app.services.storage.service import LocalStorageService, S3StorageService

    if isinstance(storage, LocalStorageService):
        storage._safe_path(uri).unlink(missing_ok=True)
        return
    if isinstance(storage, S3StorageService):
        bucket, key = storage._parse_uri(uri)
        storage.client.delete_object(Bucket=bucket, Key=key)
        return
    storage.delete_file(uri)  # pragma: no cover - other backends


def delete_candidate_files(db: Session, agency_id: int, candidate_id: int) -> int:
    """Remove stored objects for a candidate, then their rows. Raises DocumentDeleteError."""
    docs = list_documents(db, agency_id, candidate_id)
    storage = get_storage_service()
    failed: list[int] = []
    for d in docs:
        try:
            _strict_delete(storage, d.storage_uri)
        except Exception as exc:
            logger.error("rec_document_delete_failed doc_id=%s error=%s", d.id, exc)
            failed.append(d.id)
    if failed:
        raise DocumentDeleteError(f"{len(failed)} stored file(s) could not be deleted")
    for d in docs:
        db.delete(d)
    return len(docs)


def content_disposition(filename: str) -> str:
    """Attachment header with a sanitised ASCII filename (no header injection)."""
    return f'attachment; filename="{secure_filename(filename)}"'
