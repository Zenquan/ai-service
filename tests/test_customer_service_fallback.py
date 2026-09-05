"""customer_service 存储回退路径测试：MySQL 配置存在但不可达时显性回退内存。"""
from __future__ import annotations

import pytest

from app.services import customer_service as cs_module
from app.services.chat_store import MemoryChatStore, MysqlChatStore, StoreUnavailable


class TestStoreFallback:
    def test_fallback_switches_to_memory(self, monkeypatch):
        """MySQL 操作抛 StoreUnavailable 后，服务切换到内存存储并标记 memory_fallback。"""
        monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
        monkeypatch.setenv("MYSQL_DB", "testdb")
        service = cs_module.CustomerServiceService()

        class BrokenStore(MysqlChatStore):
            def get_or_create_conversation(self, conversation_id: str) -> dict:
                raise StoreUnavailable("模拟 MySQL 不可达")

            def list_messages(self, conversation_id: str) -> list[dict]:
                raise StoreUnavailable("模拟 MySQL 不可达")

        broken = BrokenStore()
        service._store = broken
        service._storage_mode = "mysql"
        service._fallback_store = MemoryChatStore()
        service._graph_agent = None
        service._graph_checked = True

        # 不走真实 RAG/图：打桩 rag.ask
        import app.services.rag as rag_service

        monkeypatch.setattr(
            rag_service, "ask",
            lambda query: {"answer": "测试回复", "materials": [], "citations": [],
                            "citation_valid": False, "error": None},
        )

        result = service.handle_message("fb-1", "你好")

        assert result["storage"] == "memory_fallback"
        assert service._store is service._fallback_store
        # 回退后消息仍在内存里，接口可用
        assert service.get_messages("fb-1")

    def test_memory_mode_when_no_mysql_env(self, monkeypatch):
        """无 MYSQL_HOST 时默认内存存储，storage 标记 memory。"""
        monkeypatch.delenv("MYSQL_HOST", raising=False)
        service = cs_module.CustomerServiceService()
        assert service.storage_mode == "memory"
        assert isinstance(service._store, MemoryChatStore)
