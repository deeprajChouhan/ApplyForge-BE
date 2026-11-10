"""
One-off backfill: move CVs saved by the old ingestion writer (raw paths in
rec_candidate_profiles.source_file) into the StorageService + a
CandidateDocument row, and reduce source_file to the bare filename so no
server path is stored or shown. Idempotent and safe to re-run.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.recruiter.models import CandidateDocument, CandidateProfile
from app.recruiter.services import documents as documents_service

logger = logging.getLogger(__name__)


def backfill_legacy_cv_files(db: Session) -> dict:
    root = Path(settings.upload_dir).resolve()
    moved = missing = skipped = 0
    moved_paths: list[Path] = []
    rows = [
        c for c in db.query(CandidateProfile).filter(CandidateProfile.source_file.isnot(None)).all()
        if "/" in (c.source_file or "") or "\\" in (c.source_file or "")
    ]
    for cand in rows:
        raw = cand.source_file or ""
        base = raw.replace("\\", "/").split("/")[-1]
        has_doc = (
            db.query(CandidateDocument.id)
            .filter(CandidateDocument.candidate_id == cand.id, CandidateDocument.agency_id == cand.agency_id)
            .first()
        )
        if has_doc:
            cand.source_file = base
            skipped += 1
            continue
        # The old writer used <upload_dir>/recruiter/<agency_id>/<filename>; resolve
        # against the configured upload root, never the process cwd.
        agency_dir = (root / "recruiter" / str(cand.agency_id)).resolve()
        path = (agency_dir / base).resolve()
        if agency_dir not in path.parents or not path.is_file():
            # Leave source_file as-is so a later run (e.g. from the right host) can still find it.
            missing += 1
            continue
        try:
            vfile = documents_service.validate_upload(base, path.read_bytes())
            documents_service.store_document(
                db, agency_id=cand.agency_id, candidate_id=cand.id, file=vfile, uploaded_via="bulk_cv"
            )
            db.flush()
            cand.source_file = base
            moved += 1
            moved_paths.append(path)
        except Exception as exc:
            logger.warning("legacy_cv_backfill_failed candidate=%s error=%s", cand.id, exc)
            missing += 1
    db.commit()
    # Only after the new copies are committed: remove the unreferenced originals
    # so erasure can't miss them later.
    for p in moved_paths:
        try:
            p.unlink(missing_ok=True)
        except Exception as exc:
            logger.error("legacy_cv_original_delete_failed path=%s error=%s", p.name, exc)
    return {"moved": moved, "missing": missing, "already_migrated": skipped, "scanned": len(rows)}
