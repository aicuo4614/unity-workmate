"""验证摘要保留可追溯引用、不伪造计时或 EID，并覆盖未知格式。"""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('rdoc_data_under_test', Path(__file__).parents[1] / 'tools' / 'renderdoc_data.py')
data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(data)


class DataContractTests(unittest.TestCase):
    def test_summary_keeps_small_draw_in_full_index(self):
        detail = data.new_detail({'name': 'frame.rdc', 'sha256': 'test'})
        for eid in range(1, 32):
            detail['draws'].append({'event_id': eid, 'chunk_index': eid + 20, 'kind': 'draw',
                'name': 'Draw', 'path': ['UI' if eid == 31 else 'Scene'], 'triangles': 1000 - eid,
                'gpu_ms': None, 'shader_id': '1', 'shader_name': 'Lit'})
            detail['events'].append({'event_id': eid})
        data.aggregate(detail)
        before = copy.deepcopy(detail)
        a = data.build_aidata(detail, limit=2)
        self.assertEqual(31, a['overview']['draw_calls'])
        self.assertEqual(2, len(a['largest_geometry_draws']))
        self.assertEqual(31, detail['events'][-1]['event_id'])
        self.assertTrue(a['selection']['all_events_retained_in_detaildata'])
        self.assertEqual(before, detail)
        self.assertEqual(a, data.build_aidata(detail, limit=2))
        self.assertIsNone(a['overview']['gpu_ms'])
        self.assertTrue(all(row['gpu_ms'] is None for row in a['largest_geometry_draws']))

    def test_structured_fallback_has_no_invented_event_ids(self):
        xml = '''<rdc><header><driver>OpenGLES</driver></header><chunks>
        <chunk name="Internal::Beginning of Capture" chunkIndex="100"/>
        <chunk name="glPushDebugGroup" chunkIndex="101"><string name="message">UI</string></chunk>
        <chunk name="glDrawElements" chunkIndex="102"><enum name="mode" string="GL_TRIANGLES"/>
        <int name="count">6</int></chunk><chunk name="glPopDebugGroup" chunkIndex="103"/>
        <chunk name="Internal::End of Capture" chunkIndex="104"/></chunks></rdc>'''
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[2]) as folder:
            path = Path(folder) / 'capture.xml'
            path.write_text(xml, encoding='utf-8')
            detail = data.parse_xml(path, {})
        self.assertEqual(1, detail['overview']['draw_calls'])
        self.assertEqual(2, detail['overview']['submitted_triangles'])
        self.assertEqual(102, detail['draws'][0]['chunk_index'])
        self.assertIsNone(detail['draws'][0]['event_id'])
        self.assertFalse(detail['coverage']['event_ids'])
        self.assertFalse(detail['capabilities']['pipeline'])

    def test_texture_estimate_is_logical_and_unknown_stays_unknown(self):
        tex = {'format': 'GL_RGB8', 'target': 'GL_TEXTURE_CUBE_MAP', 'width': 2048,
               'height': 2048, 'depth': 1, 'mips': 1}
        estimate = data.estimate_texture(tex)
        self.assertEqual(72 * 1024**2, estimate['size_bytes'])
        self.assertIn('logical', estimate['size_basis'])
        tex['format'] = 'future-unknown-format'
        self.assertEqual({}, data.estimate_texture(tex))

    def test_unsupported_structured_api_reports_unavailable_counts(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[2]) as folder:
            path = Path(folder) / 'capture.xml'
            path.write_text('<rdc><header><driver>Vulkan</driver></header><chunks/></rdc>')
            detail = data.parse_xml(path, {})
        self.assertIsNone(detail['overview']['draw_calls'])
        self.assertIsNone(detail['overview']['submitted_triangles'])


if __name__ == '__main__':
    unittest.main()
