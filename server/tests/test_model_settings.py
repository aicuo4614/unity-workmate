"""验证配置持久化、接口脱敏和跨轮快照；不访问真实模型。"""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.infra import model_manager as manager
from server.model_api import router


class ModelSettingsTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[1] / "data"
        temp_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.path_patch = patch.object(manager, "_CONFIG_PATH", Path(self.temp.name) / "model.json")
        self.path_patch.start()
        self.config_patch = patch.object(manager, "_config", manager.ModelConfig())
        self.config_patch.start()
        self.model_patch = patch.object(manager, "_current_model", None)
        self.model_patch.start()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.model_patch.stop()
        self.config_patch.stop()
        self.path_patch.stop()
        self.temp.cleanup()

    def test_missing_key_status_and_request(self):
        self.assertFalse(self.client.get("/settings/model").json()["configured"])
        with self.assertRaisesRegex(ValueError, "API Key"):
            manager.get_model()

    def test_save_keep_replace_clear_and_restart(self):
        key = "test-private-key-123456"
        response = self.client.put("/settings/model", json={"api_key": key})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(key, response.text)
        self.assertNotIn("api_key", response.json())
        self.assertTrue(response.json()["configured"])
        manager.save_config(manager.ModelConfig(model="another-model"))
        self.assertEqual(manager._key(manager._load()), key)
        manager.save_config(manager.ModelConfig(api_key="replacement-key"))
        self.assertEqual(manager._key(manager._load()), "replacement-key")
        manager.save_config(manager.ModelConfig(api_key=""))
        manager._config = None
        self.assertFalse(manager.public_config()["configured"])
        self.assertEqual(json.loads(manager._CONFIG_PATH.read_text())["api_key"], "")

    def test_atomic_save_failure_keeps_active_config(self):
        manager.save_config(manager.ModelConfig(api_key="original-key"))
        with patch.object(manager.os, "replace", side_effect=OSError("disk error")):
            response = self.client.put("/settings/model", json={"api_key": "new-secret-key"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("new-secret-key", response.text)
        self.assertEqual(manager._key(manager._load()), "original-key")

    def test_official_endpoint_validation(self):
        response = self.client.put("/settings/model", json={"api_key": "private-key", "base_url": "https://other.example"})
        self.assertEqual(response.status_code, 422)
        self.assertFalse(manager._CONFIG_PATH.exists())

    def test_connection_error_is_sanitized_and_does_not_save(self):
        class BrokenModel:
            async def ainvoke(self, *args, **kwargs):
                raise RuntimeError("secret-upstream-error")
        with patch.object(manager, "_build", return_value=BrokenModel()):
            response = self.client.post("/settings/model/test", json={"api_key": "private-test-key"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("secret-upstream-error", response.text)
        self.assertNotIn("private-test-key", response.text)
        self.assertFalse(manager._CONFIG_PATH.exists())

    def test_test_uses_unsaved_values_and_preserves_saved_key(self):
        manager.save_config(manager.ModelConfig(api_key="saved-key"))
        seen = []
        class FakeModel:
            async def ainvoke(self, *args, **kwargs):
                return "OK"
        def build(config):
            seen.append((manager._key(config), config.model))
            return FakeModel()
        with patch.object(manager, "_build", side_effect=build):
            response = self.client.post("/settings/model/test", json={"model": "draft-model"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen, [("saved-key", "draft-model")])
        self.assertEqual(manager.public_config()["model"], "deepseek-chat")

    def test_running_stream_and_invoke_keep_snapshot(self):
        async def scenario():
            first, second = object(), object()
            manager._current_model = first
            @manager.with_model_snapshot
            async def stream():
                yield manager.get_model()
                await asyncio.sleep(0)
                yield manager.get_model()
            it = stream()
            self.assertIs(await anext(it), first)
            # 更新发生在独立 HTTP 请求的上下文，不能继承流的 ContextVar。
            async def update():
                manager._current_model = second
            context = __import__("contextvars").Context()
            await asyncio.create_task(update(), context=context)
            self.assertIs(await anext(it), first)
            await it.aclose()
            self.assertIs(manager.get_model(), second)
            @manager.with_model_snapshot
            async def invoke():
                self.assertIs(manager.get_model(), second)
                manager._current_model = first
                return manager.get_model()
            self.assertIs(await invoke(), second)
            self.assertIs(manager.get_model(), first)
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
