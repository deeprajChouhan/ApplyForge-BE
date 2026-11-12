"""
Merge a role that the careers-site sync created by mistake into the role the
agency already had, and move the portal's job reference across.

Background: before the fix in partner/routes.py (upsert_job adopts unlinked
roles), PUT /partner/v1/jobs/by-external-ref/{ref} created a *new* role when
the agency's own role had no external_ref. Applications from the careers site
then landed on that duplicate.

What this does, for one agency:
  1. Clears external_ref on the duplicate and sets it on the role to keep.
  2. Moves applications (and any consents / submissions / interviews / offers /
     placements tied to them) from the duplicate to the kept role.
  3. Leaves the duplicate closed. Pass --delete-duplicate to remove it.

Dry run by default — nothing is written unless you pass --apply.

Prefer the admin panel: Admin → Ops tools does the same thing with a preview
and an audit trail. This script is the fallback when the API is down.

Run from backend/:
    python scripts/merge_duplicate_role.py --ref core-automotive-embedded-software-developer
    python scripts/merge_duplicate_role.py --ref core-automotive-embedded-software-developer --apply

If the agency has more than one candidate role to keep, pass --keep <role id>
(public id from the URL or raw integer).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import SessionLocal  # noqa: E402
from app.recruiter import models as M  # noqa: E402
from app.recruiter.enums import RoleStatus  # noqa: E402
from app.recruiter.ids import decode_id  # noqa: E402

# Role-scoped rows that belong to an application and should follow it.
FOLLOW_TABLES = [
    "CandidateConsent",
    "ClientSubmission",
    "Interview",
    "Offer",
    "Placement",
]


def _id(v: str) -> int:
    return int(v) if v.isdigit() else decode_id(v)


def _slug(v: str | None) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", (v or "").strip().lower()).strip("-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, help="external_ref the careers site uses")
    ap.add_argument("--agency", help="agency id (only needed if the ref exists in several agencies)")
    ap.add_argument("--keep", help="role id to keep (defaults to the single unlinked role with the same title)")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--delete-duplicate", action="store_true", help="delete the duplicate role after merging")
    a = ap.parse_args()

    db = SessionLocal()
    try:
        q = db.query(M.Role).filter(M.Role.external_ref == a.ref)
        if a.agency:
            q = q.filter(M.Role.agency_id == _id(a.agency))
        dups = q.all()
        if len(dups) != 1:
            print(f"[!] Expected exactly one role with external_ref={a.ref!r}, found {len(dups)}. Pass --agency.")
            return 1
        dup = dups[0]

        if a.keep:
            keep = db.get(M.Role, _id(a.keep))
            if keep is None or keep.agency_id != dup.agency_id:
                print("[!] --keep role not found in the same agency.")
                return 1
        else:
            cands = [
                r for r in db.query(M.Role).filter(
                    M.Role.agency_id == dup.agency_id, M.Role.id != dup.id, M.Role.external_ref.is_(None)
                ).all()
                if _slug(r.title) in {_slug(dup.title), _slug(a.ref)}
            ]
            if len(cands) != 1:
                print(f"[!] Found {len(cands)} candidate roles to keep: "
                      + ", ".join(f"#{r.id} {r.title!r} ({r.status.value})" for r in cands)
                      + ". Pass --keep.")
                return 1
            keep = cands[0]

        print(f"Duplicate : #{dup.id} {dup.title!r} status={dup.status.value} source={dup.source}")
        print(f"Keep      : #{keep.id} {keep.title!r} status={keep.status.value} source={keep.source}")
        if keep.status != RoleStatus.open:
            print("[!] Note: the role to keep is not open — the careers site will get job_closed until you reopen it.")

        apps = db.query(M.Application).filter(M.Application.role_id == dup.id).all()
        existing = {
            c for (c,) in db.query(M.Application.candidate_id).filter(M.Application.role_id == keep.id).all()
        }
        clashes = [x for x in apps if x.candidate_id in existing]
        print(f"Applications to move: {len(apps)}")
        for x in apps:
            print(f"  - app #{x.id} candidate #{x.candidate_id} stage={x.stage.value if x.stage else None}"
                  + ("  [ALREADY ON KEPT ROLE — will be left on duplicate]" if x in clashes else ""))

        follow_counts = {}
        for name in FOLLOW_TABLES:
            model = getattr(M, name, None)
            if model is None or not hasattr(model, "role_id"):
                continue
            follow_counts[name] = db.query(model).filter(model.role_id == dup.id).count()
        print("Related rows to move:", {k: v for k, v in follow_counts.items() if v} or "none")

        if not a.apply:
            print("\nDry run — nothing written. Re-run with --apply.")
            return 0

        dup.external_ref = None
        db.flush()  # free the unique (agency_id, external_ref) slot first
        keep.external_ref = a.ref
        for x in apps:
            if x not in clashes:
                x.role_id = keep.id
        for name in follow_counts:
            model = getattr(M, name)
            db.query(model).filter(model.role_id == dup.id).update(
                {model.role_id: keep.id}, synchronize_session=False
            )
        if a.delete_duplicate:
            if clashes:
                print("[!] Not deleting the duplicate: it still holds applications that clash.")
            else:
                db.delete(dup)
        db.commit()
        print("\nDone.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
