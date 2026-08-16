"""图片描述接口——文档图片 → 中文描述（T2，可插拔视觉模型）"""
from abc import ABC, abstractmethod
from pathlib import Path


class ImageCaptioner(ABC):
    """将图片文件转为中文描述（失败/无能力返回空串，调用方降级）"""

    @abstractmethod
    async def caption(self, image_path: Path) -> str:
        """返回中文描述；无法描述时返回 ""（调用方用文件名占位）"""
        ...
