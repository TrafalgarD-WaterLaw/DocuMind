"""图片描述服务——QwenVL（阿里云百炼 OpenAI 兼容）/ Noop 双实现

QwenVL 需要 .env 配置 DASHSCOPE_API_KEY；无 key 时 di 选择 NoopCaptioner，
文档图片链路降级为「文件名占位」（图片仍导出与展示，T3 不受影响）。
"""
from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
from pathlib import Path

from openai import AsyncOpenAI

from interfaces.image_captioner import ImageCaptioner

logger = logging.getLogger(__name__)

_CAPTION_PROMPT = (
    "请用不超过 40 字的中文概括这张文档图片：主体内容、可辨识的纹饰或文字。"
    "若无法辨认则只回答：图片内容无法辨认。"
)


class QwenVLCaptioner(ImageCaptioner):
    """阿里云百炼 Qwen-VL（OpenAI 兼容多模态接口）"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: str = "qwen-vl-max-latest",
    ):
        self.model = model
        self.client = AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=60.0
        )

    async def caption(self, image_path: Path) -> str:
        try:
            # 文件读经 to_thread——不阻塞事件循环（read_bytes 同步 I/O 残余）
            b64 = base64.b64encode(
                await asyncio.to_thread(image_path.read_bytes)
            ).decode("ascii")
            mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
            url = f"data:{mime};base64,{b64}"
            messages = [
                {"role": "system", "content": _CAPTION_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": url},
                        }
                    ],
                },
            ]
            completion = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
                max_tokens=128,
                # 推理模型关思维链:图注任务无推理需求,实测 qwen3.7-plus
                # 开启时 34s/张 + 1851 推理 token,关闭后 1.4s/张 +
                # 0 推理 token,描述质量不变;非推理模型网关自动忽略
                extra_body={"enable_thinking": False},
            )
            text = (completion.choices[0].message.content or "").strip()
            return text
        except Exception as e:
            logger.warning(f"QwenVL 图片描述失败 ({image_path.name}): {e}")
            return ""


class NoopCaptioner(ImageCaptioner):
    """无视觉能力时的降级实现"""

    async def caption(self, image_path: Path) -> str:
        return ""
