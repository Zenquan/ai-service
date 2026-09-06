"""chat_store 存储层测试（不依赖真实 MySQL）。"""
from __future__ import annotations

import pytest

from server.services.chat_store import (
    HISTORY_LIMIT,
    MemoryChatStore,
    MysqlChatStore,
    StoreUnavailable,
)

class TestMemoryChatStore:
    def test_roundtrip(self):
        store = MemoryChatStore()
        store.get_or_create_conversation("c-1")
        store.append_message(
            "c-1",
            {
                "id": "m-1",
                "role": "user",
                "content": "你好",
                "created_at": "2026-09-05T00:00:00+00:00",
            },
        )
        messages = store.list_messages("c-1")
        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    def test_missing_conversation_returns_empty(self):
        store = MemoryChatStore()
        assert store.list_messages("nope") == []

    def test_update_status(self):
        store = MemoryChatStore()
        store.get_or_create_conversation("c-1")
        store.update_conversation("c-1", status="handoff", handoff_reason="业务工具尚未接入")
        conversation = store.get_conversation("c-1")
        assert conversation["status"] == "handoff"
        assert conversation["handoff_reason"] == "业务工具尚未接入"

    def test_checkpoint_roundtrip(self):
        store = MemoryChatStore()
        store.save_checkpoint("c-1", {"intent": "greeting", "slots": {"order_no": "A00001"}})
        checkpoint = store.load_checkpoint("c-1")
        assert checkpoint == {"intent": "greeting", "slots": {"order_no": "A00001"}}
        # 返回副本，外部改动不影响存储
        checkpoint["intent"] = "order_query"
        assert store.load_checkpoint("c-1")["intent"] == "greeting"

class TestMysqlChatStoreSelection:
    def test_requires_host(self, monkeypatch):
        monkeypatch.delenv("MYSQL_HOST", raising=False)
        with pytest.raises(StoreUnavailable):
            MysqlChatStore()

    def test_default_user_is_zenquan(self, monkeypatch):
        monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
        store = MysqlChatStore()
        assert store._config["user"] == "zenquan"
        assert store._config["port"] == 3306
