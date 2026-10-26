"""健康检查测试（§4.2 44/45：/healthz、/readyz 裸格式 + 依赖探活）。"""


def test_healthz_shape(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded", "starting")
    assert set(body["deps"].keys()) >= {"db", "redis", "minio"}


def test_readyz_shape(client):
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert "status" in resp.json()


def test_metrics_available(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "python_info" in resp.text or "process_" in resp.text


def test_healthz_does_not_block(client):
    """healthz 只读快照：连续多次调用均快速返回（慢依赖不阻塞事件循环）。"""
    import time

    t0 = time.perf_counter()
    for _ in range(5):
        assert client.get("/healthz").status_code == 200
    assert time.perf_counter() - t0 < 3.0
