"""提示词管理——从 prompts/*.md 加载，支持 {占位符} 模板

每个提示词一个 .md 文件，用 `## SYSTEM` / `## USER` 分隔两个角色消息。
动态参数用 {name} 占位符（render 时传入）；模板中的字面花括号需转义为 {{ }}。

渲染器实例由容器装配（core.di.container.renderer）；模块函数
render_system/render_user 为无状态委托入口。
"""
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


class PromptRenderer:
    """提示词模板渲染器——mtime 失效缓存为实例状态（容器装配唯一实例）"""

    def __init__(self, prompts_dir: Path | None = None) -> None:
        self._prompts_dir = prompts_dir or _PROMPTS_DIR
        self._cache: dict[str, tuple[float, dict[str, str]]] = {}

    def load(self, name: str) -> dict[str, str]:
        """读取提示词模板，返回 {system, user} 两个部分（按 mtime 缓存，文件修改后自动刷新）"""
        path = self._prompts_dir / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"提示词文件不存在: {path}")
        mtime = path.stat().st_mtime
        cached = self._cache.get(name)
        if cached and cached[0] == mtime:
            return cached[1]

        text = path.read_text(encoding="utf-8").strip()
        parts: dict[str, str] = {}
        current: str | None = None
        for line in text.splitlines():
            if line.startswith("## "):
                current = line[3:].strip().lower()
                parts[current] = ""
            elif current and current in ("system", "user"):
                parts[current] += line + "\n"
        for key in ("system", "user"):
            parts.setdefault(key, "")
            parts[key] = parts[key].strip()
        self._cache[name] = (mtime, parts)
        return parts

    def render_system(self, name: str, **kwargs) -> str:
        return self.load(name)["system"].format(**kwargs)

    def render_user(self, name: str, **kwargs) -> str:
        return self.load(name)["user"].format(**kwargs)

    def render(self, name: str, **kwargs) -> str:
        """读取并渲染完整模板（system + user 拼接）"""
        parts = self.load(name)
        return "\n\n".join(
            parts[k].format(**kwargs) for k in ("system", "user") if parts[k]
        )


def _default_renderer() -> PromptRenderer:
    """组合根装配的默认渲染器——状态归容器，委托函数保持无状态"""
    from core.di import container

    return container.renderer


def render_system(name: str, **kwargs) -> str:
    return _default_renderer().render_system(name, **kwargs)


def render_user(name: str, **kwargs) -> str:
    return _default_renderer().render_user(name, **kwargs)
