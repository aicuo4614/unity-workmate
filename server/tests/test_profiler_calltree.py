"""验证单帧调用树的会话隔离和历史文件定位；不依赖 Unity 进程。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.tools import profiler_capture as capture


class CallTreeTests(unittest.TestCase):
    def test_history_tree_requires_matching_session_and_frame(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[2]) as folder:
            root = Path(folder)
            raw = root / "session1__import-abc_20261029_120000.raw"
            raw.write_bytes(b"raw")
            directory = Path(str(raw.with_suffix(".detaildata")) + ".calls")
            directory.mkdir()
            tree = directory / "289.json"
            tree.write_text(json.dumps({"frame": 289, "nodes": []}), encoding="utf-8")
            with patch.object(capture, "_profiler_dir", return_value=root):
                self.assertEqual(capture.profiler_calltree_file(raw.name, "session1", 289), tree)
                for name, session, frame in (
                    (raw.name, "session2", 289),
                    (raw.name, "", 289),
                    (raw.name, "session1", -1),
                    (raw.name, "session1", 290),
                    ("../" + raw.name, "session1", 289),
                    ("", "session1", 289),
                ):
                    self.assertIsNone(capture.profiler_calltree_file(name, session, frame))
                # 老分析没有调用树时返回缺失，保留原有排行可读。
                tree.unlink()
                self.assertIsNone(capture.profiler_calltree_file(raw.name, "session1", 289))

    def test_calltree_does_not_enter_ai_summary(self):
        detail = {"frames": [{
            "frame": 289, "cpu_ms": 12, "call_tree_available": True,
            "cpu_top": [{"name": "Animators.WriteJob", "ms": 3}],
        }]}
        summary = capture.build_aidata_from_detaildata(detail)
        self.assertEqual(summary["top_frames"][0]["frame"], 289)
        self.assertNotIn("call_tree", json.dumps(summary))
