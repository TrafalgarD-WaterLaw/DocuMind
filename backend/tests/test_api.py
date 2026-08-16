"""API 端点测试——健康检查与聊天流式输出"""
import json

import pytest


class TestHealth:
    """健康检查端点"""

    @pytest.mark.asyncio
    async def test_health_ok(self, client):
        """GET /api/health 返回 200 和 {"status": "ok"}"""
        response = await client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_method_not_allowed(self, client):
        """POST /api/health 返回 405"""
        response = await client.post("/api/health")
        assert response.status_code == 405


class TestChat:
    """聊天端点"""

    @pytest.mark.asyncio
    async def test_chat_stream_ndjson(self, client):
        """POST /api/chat 返回 NDJSON 流，包含多行 JSON 事件"""
        response = await client.post(
            "/api/chat",
            json={"query": "请介绍汝窑青瓷的特点"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")

        # 解析 NDJSON 行
        lines = response.text.strip().split("\n")
        assert len(lines) >= 3, f"Expected at least 3 NDJSON lines, got {len(lines)}"

        events = []
        for line in lines:
            if line.strip():
                event = json.loads(line)
                events.append(event)
                assert "type" in event
                assert "data" in event
                assert "timestamp" in event

        # 验证事件类型多样性（reasoning 属 P1 功能未实现，不在此断言）
        event_types = [e["type"] for e in events]
        assert "zero_result" in event_types

    @pytest.mark.asyncio
    async def test_chat_empty_query(self, client):
        """POST /api/chat 空查询返回 400"""
        response = await client.post(
            "/api/chat",
            json={"query": ""},
        )
        assert response.status_code == 422  # Pydantic 校验 min_length=1

    @pytest.mark.asyncio
    async def test_chat_missing_query(self, client):
        """POST /api/chat 缺少 query 字段返回 422"""
        response = await client.post(
            "/api/chat",
            json={},
        )
        assert response.status_code == 422


class TestDocumentDelete:
    """文档删除安全防护（H1）"""

    @pytest.mark.asyncio
    async def test_delete_path_traversal_blocked(self, client):
        """H1: 路径穿越防护——%2F 编码穿越与 .. 逃逸一律拒绝

        历史漏洞: {source:path} 路由 + uvicorn unquote(%2F) 可构造
        `..%2F..%2Fdata` 穿到 _cleanup_source_images 的 rmtree。
        """
        # %2F 编码斜杠穿越（uvicorn 会 unquote 成 / 再匹配路由）
        r1 = await client.delete("/api/documents/..%2F..%2Fdata%2Fxxx")
        assert r1.status_code in (400, 404)
        # 字面 .. 路径组件
        r2 = await client.delete("/api/documents/123_test.pdf/../../data")
        assert r2.status_code in (400, 404)
        # %2e 编码点 + %2f 斜杠
        r3 = await client.delete("/api/documents/%2e%2e%2f%2e%2e")
        assert r3.status_code in (400, 404)
        # 带反斜杠（Windows 分隔符）
        r4 = await client.delete("/api/documents/..%5C..%5Cdata")
        assert r4.status_code in (400, 404)


class TestKnowledge:
    """知识图谱端点"""

    @pytest.mark.asyncio
    async def test_init_graph(self, client):
        """GET /api/knowledge/init 返回 echarts_data 和 nodes_relation"""
        response = await client.get("/api/knowledge/init")
        # Neo4j 可能不可用，测试数据结构合理性即可
        if response.status_code == 200:
            data = response.json()
            assert "echarts_data" in data
            assert "nodes_relation" in data
            assert isinstance(data["echarts_data"], list)
            assert isinstance(data["nodes_relation"], list)
        else:
            assert response.status_code == 500  # Neo4j 不可用时返回 500

    @pytest.mark.asyncio
    async def test_expand_missing_fields(self, client):
        """expand 字段均有默认值（cypher_query 为历史命名,expand 不使用）;
        空请求过校验,图谱不可用时返回 500"""
        response = await client.post(
            "/api/knowledge/expand",
            json={},
        )
        assert response.status_code == 500  # 测试环境 Neo4j 不可用

    @pytest.mark.asyncio
    async def test_search_missing_fields(self, client):
        """search 字段均有默认值;空请求过校验,图谱不可用时返回 500"""
        response = await client.post(
            "/api/knowledge/search",
            json={},
        )
        assert response.status_code == 500  # 测试环境 Neo4j 不可用
