"""冒烟测试——验证 pytest 基础设施与 src 导入路径"""
from core.config import settings


def test_settings_loads():
    assert settings.chunk_size == 500
    assert settings.chunk_overlap == 50


def test_import_providers():
    from conversation.infrastructure.deepseek_llm import DeepSeekProvider  # noqa: F401
    from retrieval.bm25 import BM25Index  # noqa: F401
