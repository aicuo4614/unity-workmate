"""验证外部 raw 导入、会话历史和失败清理，不启动 Unity 或连接设备。"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from server.tools import profiler_capture as capture
from common.models import AndroidProfilerResponse, AndroidProfilerHistoryResponse

class Stream:
    def __init__(self, content):
        self.content = content
    async def stream(self):
        yield self.content[:4]
        yield self.content[4:]

def inline_async(test):
    """这些用例无需网络；内联文件 IO，避免 Windows 沙箱的事件循环 socketpair。"""
    async def inline_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    def run(self):
        with patch.object(asyncio, "to_thread", inline_thread):
            coro = test(self)
            try:
                coro.send(None)
            except StopIteration:
                return
            finally:
                coro.close()
            self.fail("测试遇到未预期的异步等待")
    return run


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parents[2])
        self.root = Path(self.temp.name)
        self.saved = dict(capture._state)
        capture._state.clear()
        self.patcher = patch.object(capture, '_profiler_dir', return_value=self.root)
        self.patcher.start()
    def tearDown(self):
        self.patcher.stop()
        capture._state.clear()
        capture._state.update(self.saved)
        self.temp.cleanup()
    @inline_async
    async def test_import_repeated_name_history_reload_and_isolation(self):
        data = b'PD3U' + b'2022.3.62f3 Main Thread' * 10
        first = await capture.import_profiler_raw(Stream(data), r'C:\captures\test.RAW', 'session1')
        second = await capture.import_profiler_raw(Stream(data), 'test.RAW', 'session1')
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(Path(first['local_path']).read_bytes(), data)
        self.assertEqual(first['summary']['unity_version'], '2022.3.62f3')
        self.assertEqual(AndroidProfilerResponse(**first).original_name, 'test.RAW')
        items = AndroidProfilerHistoryResponse(**capture.list_profiler_history(thread_id='session1')).items
        self.assertEqual(len(items), 2)
        self.assertTrue(all(x.source == 'import' and x.serial == '' for x in items))
        self.assertEqual(capture.list_profiler_history(thread_id='session2')['items'], [])
        self.assertFalse(capture.load_profiler_history(first['id'], 'session2')['ok'])
        capture._state.clear()
        loaded = capture.load_profiler_history(first['id'], 'session1')
        self.assertEqual(loaded['original_name'], 'test.RAW')
        self.assertEqual(loaded['shots'], [])
        self.assertEqual(capture._resolve_session_raw(first['id'], 'session1')[0], Path(first['local_path']))
    @inline_async
    async def test_invalid_and_interrupted_upload_leave_no_history(self):
        with self.assertRaises(HTTPException) as error:
            await capture.import_profiler_raw(Stream(b''), 'test.raw', 'session1')
        self.assertEqual(error.exception.status_code, 400)
        self.assertIn('文件为空', error.exception.detail)
        class Broken:
            async def stream(self):
                yield b'PD3U'
                raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await capture.import_profiler_raw(Broken(), 'test.raw', 'session1')
        self.assertEqual(list(self.root.iterdir()), [])
    @inline_async
    async def test_unrecognized_header_is_imported_with_warning(self):
        data = b'not a recognized profiler header'
        imported = await capture.import_profiler_raw(Stream(data), 'test.raw', 'session1')
        self.assertTrue(imported['ok'])
        self.assertFalse(imported['summary']['valid_magic'])
        self.assertTrue(any('PD3U' in note for note in imported['summary']['notes']))
        self.assertEqual(Path(imported['local_path']).read_bytes(), data)
        capture._state.clear()
        loaded = capture.load_profiler_history(imported['id'], 'session1')
        self.assertTrue(loaded['ok'])
        self.assertFalse(loaded['summary']['valid_magic'])

    @inline_async
    async def test_bad_extension_and_missing_session_are_rejected(self):
        for name, tid in [('test.txt', 'session1'), ('test.raw', '')]:
            with self.assertRaises(HTTPException):
                await capture.import_profiler_raw(Stream(b'PD3U'), name, tid)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_version_uses_unique_editor_without_claiming_exact_match(self):
        editors = [{"version": "2022.3.62f3c1", "exe": "C:/Unity/Editor/Unity.exe"}]
        with patch.object(capture, '_hub_editors', return_value=editors), patch.object(
            capture, '_running_unity_exes', return_value=set()
        ):
            matched = capture._match_hub_editor('')
            self.assertEqual(matched['match'], 'fallback')
            self.assertEqual(matched['version'], editors[0]['version'])

    def test_missing_version_does_not_guess_between_versions(self):
        editors = [
            {"version": "2022.3.62f3", "exe": "C:/UnityA/Unity.exe"},
            {"version": "2021.3.1f1", "exe": "C:/UnityB/Unity.exe"},
        ]
        with patch.object(capture, '_hub_editors', return_value=editors), patch.object(
            capture, '_running_unity_exes', return_value=set()
        ):
            self.assertEqual(capture._match_hub_editor('')['match'], 'none')
        with patch.object(capture, '_hub_editors', return_value=editors), patch.object(
            capture, '_running_unity_exes', return_value={capture._path_key(editors[0]['exe'])}
        ):
            self.assertEqual(capture._match_hub_editor('')['version'], editors[0]['version'])

    @inline_async
    async def test_busy_does_not_replace_capture(self):
        capture._state.update(status='recording', id='live')
        with self.assertRaises(HTTPException) as error:
            await capture.import_profiler_raw(Stream(b'PD3U'), 'test.raw', 'session1')
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(capture._state['id'], 'live')

if __name__ == '__main__':
    unittest.main()
