"""验证指定帧提取、顺序、持久化及错误时不改数据；不依赖 Unity。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from server.tools import profiler_capture as capture


class CustomFramesTests(unittest.TestCase):
    def test_selection_and_invalid_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = Path(folder) / "test.raw"
            raw.write_bytes(b"PD3U")
            detail = {"frames": [{"frame": 228, "cpu_ms": 25}, {"frame": 270, "cpu_ms": 50}]}
            path = raw.with_suffix(".detaildata")
            path.write_text(json.dumps(detail), encoding="utf-8")
            with patch.object(capture, "_resolve_session_raw", return_value=(raw, "")):
                result = capture.select_profiler_frames(raw.name, "session", [228, 270, 228])
                self.assertEqual(result["frames"], [228, 270])
                saved = json.loads(raw.with_suffix(".aidata").read_text(encoding="utf-8"))
                self.assertEqual([f["frame"] for f in saved["custom_frames"]], [228, 270])
                self.assertEqual(saved["top_frames"][0]["frame"], 270)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["custom_frame_numbers"], [228, 270])
                before = raw.with_suffix(".aidata").read_bytes()
                for numbers in ([999], [-1], [], [True], list(range(101))):
                    with self.assertRaises(ValueError):
                        capture.select_profiler_frames(raw.name, "session", numbers)
                self.assertEqual(before, raw.with_suffix(".aidata").read_bytes())
            with patch.object(capture, "_resolve_session_raw", return_value=(None, "wrong session")):
                with self.assertRaisesRegex(ValueError, "wrong session"):
                    capture.select_profiler_frames(raw.name, "other", [228])
