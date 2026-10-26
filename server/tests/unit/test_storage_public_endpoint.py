"""对象存储签名 URL 的内网连接地址与浏览器公开地址隔离。"""

from app.documents import storage as storage_module


def test_storage_uses_public_endpoint_for_presigned_urls(monkeypatch):
    endpoints = []

    class FakeMinio:
        def __init__(self, endpoint, **kwargs):
            endpoints.append(endpoint)

    monkeypatch.setattr(storage_module, "Minio", FakeMinio)
    monkeypatch.setattr(storage_module.settings, "minio_endpoint", "minio:9000")
    monkeypatch.setattr(
        storage_module.settings, "minio_public_endpoint", "localhost:9000", raising=False
    )

    storage_module.DocumentStorage()

    assert endpoints == ["minio:9000", "localhost:9000"]