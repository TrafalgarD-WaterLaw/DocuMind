"""pytest fixtures——使用 httpx.ASGITransport 进行异步测试"""
import os

import pytest

# 确保在导入 app 之前设置必需的环境变量，避免 Settings() 校验失败
os.environ.setdefault("DEEPSEEK_API_KEY", "test-deepseek-key")
os.environ.setdefault("DEEPSEEK_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "test-neo4j-password")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from main import app  # noqa: E402


@pytest.fixture
async def client():
    """提供 httpx AsyncClient，通过 ASGITransport 调用应用"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
