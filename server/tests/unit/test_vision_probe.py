"""视觉能力探测：支持 / 不支持 / 超时 三路径 + 测试图生成。"""

import pytest

from app.llm import vision_probe as vp


@pytest.fixture(autouse=True)
def _reset_probe():
    vp._probe_impl = None
    yield
    vp._probe_impl = None


class TestProbeImage:
    def test_generates_small_png(self):
        data = vp.build_probe_image()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        # 极小体积，探测成本可忽略
        assert len(data) < 5000

    def test_image_is_red(self):
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(vp.build_probe_image())).convert("RGB")
        assert img.size == (vp.PROBE_SIZE, vp.PROBE_SIZE)
        r, g, b = img.getpixel((10, 10))
        assert r > 200 and g < 60 and b < 60


class TestProbeDisabled:
    def test_disabled_returns_not_ok(self):
        res = vp.probe_vision(base_url="x", api_key="k", model="m", enabled=False)
        assert res.ok is False
        assert "已关闭" in res.detail

    def test_missing_model(self):
        res = vp.probe_vision(base_url="x", api_key="k", model="")
        assert res.ok is False
        assert "视觉模型" in res.detail

    def test_missing_key(self):
        res = vp.probe_vision(base_url="x", api_key="  ", model="m")
        assert res.ok is False
        assert "Key" in res.detail or "离线" in res.detail


class TestProbeSuccess:
    def test_correct_answer_reports_ok(self, monkeypatch):
        monkeypatch.setattr(vp, "_probe_impl", lambda *a: "red")
        res = vp.probe_vision(base_url="x", api_key="k", model="hy3")
        assert res.ok is True
        assert "视觉可用" in res.detail
        assert "hy3" in res.detail

    def test_chinese_color_answer_accepted(self, monkeypatch):
        monkeypatch.setattr(vp, "_probe_impl", lambda *a: "红色")
        assert vp.probe_vision(base_url="x", api_key="k", model="m").ok is True

    def test_verbose_red_answer_accepted(self, monkeypatch):
        monkeypatch.setattr(vp, "_probe_impl", lambda *a: "The color is red.")
        assert vp.probe_vision(base_url="x", api_key="k", model="m").ok is True


class TestProbeFailure:
    def test_wrong_answer_reports_not_ok(self, monkeypatch):
        """模型答非所问 → 判定不支持视觉（不误报支持）。"""
        monkeypatch.setattr(vp, "_probe_impl", lambda *a: "抱歉，我无法识别图片")
        res = vp.probe_vision(base_url="x", api_key="k", model="m")
        assert res.ok is False
        assert "不支持" in res.detail or "异常" in res.detail

    def test_too_long_answer_rejected(self, monkeypatch):
        long_answer = "red " * 50
        monkeypatch.setattr(vp, "_probe_impl", lambda *a: long_answer)
        assert vp.probe_vision(base_url="x", api_key="k", model="m").ok is False

    def test_exception_degrades_without_raising(self, monkeypatch):
        def boom(*a):
            raise RuntimeError("gateway down")

        monkeypatch.setattr(vp, "_probe_impl", boom)
        res = vp.probe_vision(base_url="x", api_key="k", model="m")
        assert res.ok is False
        assert "探测失败" in res.detail

    def test_timeout_message_mentions_timeout(self, monkeypatch):
        class FakeTimeout(Exception):
            pass

        FakeTimeout.__name__ = "APITimeoutError"

        def boom(*a):
            raise FakeTimeout("timeout")

        monkeypatch.setattr(vp, "_probe_impl", boom)
        res = vp.probe_vision(base_url="x", api_key="k", model="m")
        assert res.ok is False
        assert "超时" in res.detail

    def test_result_has_latency(self, monkeypatch):
        monkeypatch.setattr(vp, "_probe_impl", lambda *a: "red")
        res = vp.probe_vision(base_url="x", api_key="k", model="m")
        assert res.latency_ms >= 0

    def test_to_dict_shape(self, monkeypatch):
        monkeypatch.setattr(vp, "_probe_impl", lambda *a: "red")
        d = vp.probe_vision(base_url="x", api_key="k", model="m").to_dict()
        assert d["supported"] is True
        assert "ok" in d and "detail" in d
