"""MinIO 封装：put/get/签名 URL/归档（B-3 原件永不物理删除）。

key 约定：objects/{accountId}/{subjectId}/{docId}/original
          pages/{accountId}/{subjectId}/{docId}/{pageNo}.png
"""

import logging
from functools import lru_cache
from typing import Optional

from minio import Minio
from minio.error import S3Error

from app.config.settings import settings

logger = logging.getLogger("app.storage")


class DocumentStorage:
    """原件证据层（S3 兼容）。"""

    def __init__(self) -> None:
        import urllib3

        self.bucket = settings.minio_bucket
        # 显式连接/读取超时：依赖不可达时 /healthz 快速降级而非挂起（K8s 探活）
        http_client = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=2.0, read=3.0),
            retries=False,
        )
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            http_client=http_client,
        )
        public_endpoint = settings.minio_public_endpoint.strip()
        self._presign_client = self._client
        if public_endpoint and public_endpoint != settings.minio_endpoint:
            self._presign_client = Minio(
                public_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
                region="us-east-1",
                http_client=http_client,
            )
        self._bucket_ready = False

    @property
    def client(self) -> Minio:
        return self._client

    def ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        try:
            if not self._client.bucket_exists(self.bucket):
                self._client.make_bucket(self.bucket)
            self._bucket_ready = True
        except S3Error as exc:
            logger.warning("minio.ensure_bucket failed: %s", exc)
            raise

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """写入对象。"""
        self.ensure_bucket()
        from io import BytesIO

        self._client.put_object(
            self.bucket, key, BytesIO(data), length=len(data), content_type=content_type
        )

    def get(self, key: str) -> bytes:
        """读取对象字节。"""
        response = self._client.get_object(self.bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def presign_get(self, key: str, expires_seconds: Optional[int] = None) -> str:
        """签名 GET URL（带时效，E-9：过期后 MinIO 返回 403，前端刷新）。"""
        seconds = expires_seconds or settings.sign_url_expire_seconds
        from datetime import timedelta

        return self._presign_client.presigned_get_object(
            self.bucket, key, expires=timedelta(seconds=seconds)
        )

    def exists(self, key: str) -> bool:
        try:
            self._client.stat_object(self.bucket, key)
            return True
        except S3Error:
            return False


@lru_cache
def get_storage_client() -> DocumentStorage:
    """存储单例（进程内缓存；测试可 cache_clear + monkeypatch）。"""
    return DocumentStorage()


def original_key(account_id: str, subject_id: str, doc_id: str) -> str:
    return f"objects/{account_id}/{subject_id}/{doc_id}/original"


def page_key(account_id: str, subject_id: str, doc_id: str, page_no: int) -> str:
    return f"pages/{account_id}/{subject_id}/{doc_id}/{page_no}.png"
