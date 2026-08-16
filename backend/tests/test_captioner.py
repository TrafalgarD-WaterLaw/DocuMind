"""ImageCaptioner 选择与实现测试（不触网：mock OpenAI 客户端）"""
from pathlib import Path
from types import SimpleNamespace

from multimodal.image_caption import NoopCaptioner, QwenVLCaptioner


async def test_noop_returns_empty():
    c = NoopCaptioner()
    assert await c.caption(Path("x.png")) == ""


async def test_qwen_builds_multimodal_message(monkeypatch):
    c = QwenVLCaptioner(api_key="test-key", base_url="http://x", model="qwen-vl-max-latest")

    captured = {}

    class FakeCreate:
        async def __call__(self, **kwargs):
            captured["messages"] = kwargs.get("messages")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="一件青铜鼎"))],
                usage=None,
            )

    monkeypatch.setattr(c.client.chat.completions, "create", FakeCreate())
    # 1x1 PNG（最简合法图片）
    import struct
    import zlib

    def _tiny_png() -> bytes:
        raw = b"\x00" + zlib.compress(b"\xff\xff\xff\xff")  # 1x1 白色
        def chunk(t, d):
            c = struct.pack(">I", len(d)) + t + d
            return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b""))

    img_path = Path("t.png")
    img_path.write_bytes(_tiny_png())
    try:
        desc = await c.caption(img_path)
    finally:
        img_path.unlink(missing_ok=True)

    assert desc == "一件青铜鼎"
    msgs = captured["messages"]
    assert len(msgs) == 2  # system + user
    user_content = msgs[1]["content"]
    assert any(isinstance(p, dict) and p.get("type") == "image_url" for p in user_content)
    img_part = next(p for p in user_content if p.get("type") == "image_url")
    assert img_part["image_url"]["url"].startswith("data:image/png;base64,")


async def test_di_selects_captioner(monkeypatch):
    from core.config import settings
    from core.di import AppContainer
    from multimodal.image_caption import NoopCaptioner, QwenVLCaptioner

    # 无 key → Noop
    monkeypatch.setattr(settings, "dashscope_api_key", "")
    container = AppContainer()
    assert isinstance(container.captioner, NoopCaptioner)

    # 有 key → QwenVL
    monkeypatch.setattr(settings, "dashscope_api_key", "sk-test")
    container2 = AppContainer()
    assert isinstance(container2.captioner, QwenVLCaptioner)
