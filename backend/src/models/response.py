"""响应与流式事件模型"""
import json
from enum import StrEnum
from typing import Any
from pydantic import BaseModel


class StreamEventType(StrEnum):
    ZERO_RESULT = "zero_result"
    REASONING = "reasoning"
    CONTENT = "content"
    MARKDOWN_DICT = "markdown_dict"
    SOURCES = "sources"
    RECOGNITION = "recognition"
    TRACE = "trace"
    PIPELINE = "pipeline"   # 检索流水线实时事件
    ERROR = "error"


class StreamEvent(BaseModel):
    type: StreamEventType
    data: Any
    timestamp: float

    def to_ndjson(self) -> str:
        """序列化为 NDJSON 行（流式协议——api/chat、research、vision 共用）"""
        return json.dumps(self.model_dump(), ensure_ascii=False) + "\n"
