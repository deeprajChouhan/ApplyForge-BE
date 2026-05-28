"""
reset_admin_data.py
-------------------
Wipes ALL data for the admin user (role='admin') — resumes, applications,
profile sections, knowledge base, Qdrant vectors — but keeps the user record
and password hash intact so you can still log in.

Run from the backend folder with the venv active:
    python scripts/reset_admin_data.py
"""

import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Load .env from the backend folder
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

DATABASE_URL      = "mysql+pymysql://AF-prd-admin:Hawkeye%40786@191.101.80.174:3307/AF-Prd"
QDRANT_URL        = "https://applyforge-qdrant-654062-191-101-80-174.traefik.me/"
QDRANT_API_KEY    = "ooivuzh0nttbn6iv5ablaiae5zvcer8r"
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "applyforge_chunks")

engine = create_engine(DATABASE_URL, echo=False)


def run():
    with engine.begin() as conn:

        # ── 1. Find the admin user ───────────────────────────────────────────
        row = conn.execute(
            text("SELECT id, email FROM users WHERE role = 'admin' LIMIT 1")
        ).fetchone()

        if not row:
            print("No admin user found — nothing to do.")
            return

        uid, email = row.id, row.email
        print(f"\nAdmin user found: id={uid}, email={email}")
        print("Starting data wipe (user record is kept) …\n")

        # ── 2. Delete application_chat_messages (no user_id column) ─────────
        r = conn.execute(text("""
            DELETE acm
            FROM application_chat_messages acm
            JOIN application_chats ac ON acm.chat_id = ac.id
            WHERE ac.user_id = :uid
        """), {"uid": uid})
        print(f"  application_chat_messages : {r.rowcount} rows")

        # ── 3. Delete everything else that has user_id ────────────────────────
        # Order matters: children before parents, but MySQL FK cascades help too.
        tables = [
            "application_customizations",
            "application_status_history",
            "application_chats",
            "generated_documents",
            "job_applications",
            "knowledge_chunks",
            "knowledge_documents",
            "parsed_resume_data",    # CASCADE → knowledge_chunks/docs via parsed_resume_id
            "uploaded_files",
            "user_profiles",
            "work_experiences",
            "educations",
            "projects",
            "skills",
            "certifications",
            "linkedin_connections",
            "usage_ledger",
            "usage_events",
            "user_features",
            "refresh_tokens",
        ]

        for table in tables:
            try:
                r = conn.execute(
                    text(f"DELETE FROM `{table}` WHERE user_id = :uid"),
                    {"uid": uid},
                )
                print(f"  {table:<35} {r.rowcount} rows")
            except Exception as e:
                print(f"  {table:<35} SKIPPED — {e}")

        print("\nMySQL wipe complete.")

    # ── 4. Clear Qdrant vectors ───────────────────────────────────────────────
    if QDRANT_URL:
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.http.models import Filter, FieldCondition, MatchValue

            qc = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=30)
            qc.delete(
                collection_name=QDRANT_COLLECTION,
                points_selector=Filter(
                    must=[FieldCondition(key="user_id", match=MatchValue(value=uid))]
                ),
            )
            print(f"Qdrant: deleted all vectors for user_id={uid} ✓")
        except Exception as e:
            print(f"Qdrant cleanup failed (non-fatal, you can ignore): {e}")
    else:
        print("QDRANT_URL not set — skipping vector cleanup.")

    print(f"\nDone. Admin login for '{email}' is untouched.\n")


if __name__ == "__main__":
    run()
