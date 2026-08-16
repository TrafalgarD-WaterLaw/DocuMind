"""请求模型"""
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """对话消息"""
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    """Agent 快速问答 / 聊天请求"""
    query: str = Field(..., min_length=1, description="用户提问内容")
    messages: list[ChatMessage] = Field(default_factory=list, description="历史对话")


class KnowledgeSearchRequest(BaseModel):
    """知识图谱搜索请求"""
    node_data: list = Field(default_factory=list)
    link_data: list = Field(default_factory=list)
    node_name: str = Field(default="")
    # 历史命名——实际承载"节点名搜索词"(见 api/knowledge.py search_graph 注释);
    # 默认值兼容 expand 端点(不传 cypher_query 的请求)
    cypher_query: str = Field(default="", description="文物名称搜索词")
