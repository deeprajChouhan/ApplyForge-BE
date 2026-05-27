"""
Storage service — local filesystem (dev) or S3-compatible (prod).

Selection logic (priority order):
  1. If all four S3 env vars are set → always use S3StorageService,
     regardless of ENV or STORAGE_BACKEND.
  2. Otherwise → LocalStorageService (dev / test fallback).

This means setting S3_ENDPOINT_URL + S3_ACCESS_KEY + S3_SECRET_KEY +
S3_BUCKET in .env is sufficient to enable S3; you don't need to also
flip STORAGE_BACKEND or ENV.
"""
from __future__ import annotations

import logging
import os
from io import BytesIO
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import uuid4

import boto3
from botocore.client import BaseClient
from botocore.config import Config

from app.core.config import settings

logger = logging.getLogger(__name__)


@runtime_checkable
class StorageService(Protocol):
    def upload_bytes(self, *, content: bytes, key_prefix: str, filename: str, content_type: str | None) -> str: ...
    def download_bytes(self, uri: str) -> bytes: ...
    def delete_file(self, uri: str) -> None: ...
    def generate_presigned_url(self, uri: str, expires_in: int = 3600) -> str: ...


# ── Local (dev / fallback) ───────────────────────────────────────────────────

class LocalStorageService:
    """File-system backed storage — used only when S3 credentials are absent."""

    def __init__(self) -> None:
        self.base = Path(settings.upload_dir).resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    def upload_bytes(self, *, content: bytes, key_prefix: str, filename: str, content_type: str | None) -> str:
        dest = self.base / key_prefix
        dest.mkdir(parents=True, exist_ok=True)
        key = f"{uuid4().hex}_{filename}"
        (dest / key).write_bytes(content)
        logger.debug("local_upload key=%s/%s bytes=%d", key_prefix, key, len(content))
        return f"local://{key_prefix}/{key}"

    def download_bytes(self, uri: str) -> bytes:
        _, _, tail = uri.partition("local://")
        return (self.base / tail).read_bytes()

    def delete_file(self, uri: str) -> None:
        try:
            _, _, tail = uri.partition("local://")
            path = self.base / tail
            if path.exists():
                path.unlink()
        except Exception as exc:
            logger.warning("local_delete_failed uri=%s error=%s", uri, exc)

    def generate_presigned_url(self, uri: str, expires_in: int = 3600) -> str:
        # Local storage has no real presigned URLs — return a backend proxy URL
        # The caller should fall back to the /files/download endpoint.
        return uri


# ── S3-compatible (MinIO / SeaweedFS / AWS S3) ───────────────────────────────

class S3StorageService:
    def __init__(self) -> None:
        missing = [
            name for name, val in (
                ("S3_ENDPOINT_URL", settings.s3_endpoint_url),
                ("S3_ACCESS_KEY",   settings.s3_access_key),
                ("S3_SECRET_KEY",   settings.s3_secret_key),
                ("S3_BUCKET",       settings.s3_bucket),
            ) if not val
        ]
        if missing:
            raise RuntimeError(
                f"S3StorageService requires: {', '.join(missing)}. "
                "Set them in your .env file."
            )

        self.bucket: str = settings.s3_bucket  # type: ignore[assignment]

        # Support plain-HTTP endpoints (e.g. internal Docker hostnames like
        # http://seaweedfs-s3:8333).  boto3 defaults to SSL=True which causes
        # connection failures against non-TLS endpoints.
        endpoint_url: str = settings.s3_endpoint_url  # type: ignore[assignment]
        use_ssl: bool = endpoint_url.startswith("https://")

        self.client: BaseClient = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key_value,
            region_name=settings.s3_region,
            use_ssl=use_ssl,
            verify=use_ssl,  # skip cert verification for plain HTTP
            config=Config(
                s3={"addressing_style": "path"},
                # Tight timeouts for self-hosted S3 (SeaweedFS / MinIO / RustFS)
                connect_timeout=5,      # seconds to establish TCP connection
                read_timeout=30,        # seconds to wait for response after connect
                retries={
                    "max_attempts": 2,  # 1 initial + 1 retry (vs boto3 default of 5)
                    "mode": "standard",
                },
            ),
        )
        logger.info(
            "s3_storage_init endpoint=%s bucket=%s use_ssl=%s",
            endpoint_url, self.bucket, use_ssl,
        )

    # ── Core ────────────────────────────────────────────────────────────────

    def upload_bytes(self, *, content: bytes, key_prefix: str, filename: str, content_type: str | None) -> str:
        from fastapi import HTTPException
        key = f"{key_prefix}/{uuid4().hex}_{filename}"
        extra: dict[str, str] = {}
        if content_type:
            extra["ContentType"] = content_type
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=BytesIO(content), **extra)
        except Exception as exc:
            logger.error("s3_upload_failed key=%s error=%s", key, exc)
            raise HTTPException(
                status_code=503,
                detail="File storage is temporarily unavailable. Please try again in a moment.",
            ) from exc
        uri = f"s3://{self.bucket}/{key}"
        logger.debug("s3_upload key=%s bytes=%d", key, len(content))
        return uri

    def download_bytes(self, uri: str) -> bytes:
        from fastapi import HTTPException
        bucket, key = self._parse_uri(uri)
        try:
            response = self.client.get_object(Bucket=bucket, Key=key)
            return response["Body"].read()
        except Exception as exc:
            logger.error("s3_download_failed key=%s error=%s", key, exc)
            raise HTTPException(
                status_code=503,
                detail="File could not be retrieved from storage. Please try again.",
            ) from exc

    def delete_file(self, uri: str) -> None:
        try:
            bucket, key = self._parse_uri(uri)
            self.client.delete_object(Bucket=bucket, Key=key)
            logger.debug("s3_delete key=%s", key)
        except Exception as exc:
            logger.warning("s3_delete_failed uri=%s error=%s", uri, exc)

    def generate_presigned_url(self, uri: str, expires_in: int = 3600) -> str:
        """
        Generate a time-limited pre-signed GET URL for the given S3 URI.
        expires_in: seconds until expiry (default 1 hour).
        """
        bucket, key = self._parse_uri(uri)
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        return url

    def copy_file(self, src_uri: str, dest_key_prefix: str, dest_filename: str) -> str:
        """Server-side copy within the same bucket."""
        src_bucket, src_key = self._parse_uri(src_uri)
        dest_key = f"{dest_key_prefix}/{uuid4().hex}_{dest_filename}"
        self.client.copy_object(
            Bucket=self.bucket,
            CopySource={"Bucket": src_bucket, "Key": src_key},
            Key=dest_key,
        )
        return f"s3://{self.bucket}/{dest_key}"

    # ── Internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_uri(uri: str) -> tuple[str, str]:
        """Parse 's3://bucket/key/path' → ('bucket', 'key/path')."""
        _, _, tail = uri.partition("s3://")
        bucket, _, key = tail.partition("/")
        return bucket, key


# ── Factory ──────────────────────────────────────────────────────────────────

def _s3_credentials_present() -> bool:
    return all([
        settings.s3_endpoint_url,
        settings.s3_access_key,
        settings.s3_secret_key,
        settings.s3_bucket,
    ])


def get_storage_service() -> S3StorageService | LocalStorageService:
    """
    Return S3StorageService when all four S3 env vars are set;
    otherwise fall back to LocalStorageService.

    This is credential-driven: having S3 vars set is the single source
    of truth, so you never need to also flip STORAGE_BACKEND or ENV.
    """
    if _s3_credentials_present():
        return S3StorageService()
    logger.warning(
        "storage_using_local_fallback — S3 credentials not fully configured. "
        "Set S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET to use S3."
    )
    return LocalStorageService()
