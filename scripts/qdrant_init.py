"""
One-shot init: ensure the Qdrant collection used by RAGService exists.

Run before the API starts (see docker-compose.yml's `qdrant-init` service).
Idempotent — safe to run on every deploy.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

from app.core.config import settings


def main():
    client = QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
        check_compatibility=False,
    )

    existing = {c.name for c in client.get_collections().collections}
    if settings.qdrant_collection in existing:
        print(f"[=] Collection '{settings.qdrant_collection}' already exists — skipping.")
        return

    client.create_collection(
        collection_name=settings.qdrant_collection,
        vectors_config=VectorParams(size=settings.embedding_dim, distance=Distance.COSINE),
    )
    client.create_payload_index(
        collection_name=settings.qdrant_collection,
        field_name="user_id",
        field_schema=PayloadSchemaType.INTEGER,
    )
    client.create_payload_index(
        collection_name=settings.qdrant_collection,
        field_name="parsed_resume_id",
        field_schema=PayloadSchemaType.INTEGER,
    )
    print(
        f"[+] Created collection '{settings.qdrant_collection}' "
        f"(dim={settings.embedding_dim}, distance=Cosine) with user_id/parsed_resume_id indexes."
    )


if __name__ == "__main__":
    main()
