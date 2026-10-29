"""RDC 数据与接口的语义验证，不依赖大模型。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import asyncio
from fastapi import HTTPException
from server.tools import renderdoc_capture as capture
from server.tools.renderdoc_data import aggregate, build_aidata, estimate_texture, new_detail, parse_xml
from server import renderdoc_api as api

class DataTests(unittest.TestCase):
    def test_cube_size_has_estimation_basis(self):
        t={"format":"GL_SRGB8","width":2048,"height":2048,"depth":1,"mips":1,"target":"GL_TEXTURE_CUBE_MAP"}
        self.assertEqual(estimate_texture(t)["size_bytes"],72*1024**2)
        self.assertIn("estimate",estimate_texture(t)["size_basis"])
    def test_no_invented_eid_or_gpu_time(self):
        xml='<rdc><header><driver>OpenGLES</driver></header><chunks><chunk name="glDrawElements" chunkIndex="1"><int name="count">99</int></chunk><chunk name="Internal::Beginning of Capture" chunkIndex="4"/><chunk name="glDrawElements" chunkIndex="6"><enum name="mode" string="GL_TRIANGLES">4</enum><int name="count">9</int></chunk><chunk name="Internal::End of Capture" chunkIndex="7"/></chunks></rdc>'
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[2]) as root:
            p=Path(root)/"capture.xml"; p.write_text(xml)
            d=parse_xml(p,{})
        self.assertEqual(d["overview"]["draw_calls"],1)
        self.assertEqual(d["overview"]["submitted_triangles"],3)
        self.assertIsNone(d["draws"][0]["event_id"])
        self.assertEqual(d["draws"][0]["chunk_index"],6)
        self.assertIsNone(d["overview"]["gpu_ms"])
    def test_unsupported_static_api_is_missing_not_zero(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[2]) as root:
            p=Path(root)/"capture.xml"
            p.write_text("<rdc><header><driver>Vulkan</driver></header><chunks/></rdc>")
            d=parse_xml(p,{})
        self.assertIsNone(d["overview"]["draw_calls"])
    def test_summary_keeps_stages_but_limits_draws(self):
        d=new_detail({})
        d["draws"]=[{"kind":"draw","event_id":i,"chunk_index":None,"triangles":i,"path":[str(i)],"name":"Draw","shader_name":"Shared"} for i in range(30)]
        aggregate(d); a=build_aidata(d,limit=4)
        self.assertEqual(len(a["stages"]),30)
        self.assertEqual(len(a["largest_geometry_draws"]),4)
        self.assertEqual(a["largest_geometry_draws"][0]["event_id"],29)
        self.assertEqual(a["shader_usage"][0]["draw_calls"],30)

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parents[2])
        self.root=Path(self.temp.name)
        self.patch=patch.object(capture,"ROOT",self.root); self.patch.start()
    def tearDown(self):
        self.patch.stop(); self.temp.cleanup()
    def test_reject_arbitrary_file_download(self):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(api.download("bad","job.json"))
        self.assertEqual(caught.exception.status_code,404)
        with self.assertRaises(HTTPException):
            asyncio.run(api.data("bad","source"))
    def test_reject_missing_import(self):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(api.import_capture(api.ImportRequest(path="missing.txt")))
        self.assertEqual(caught.exception.status_code,400)
    def test_empty_upload_records_failure(self):
        class Empty:
            async def stream(self):
                yield b""
        with self.assertRaises(HTTPException):
            asyncio.run(api.upload(Empty(),"test.rdc",""))
        self.assertEqual(capture.history()[0]["status"],"error")
    def test_restart_does_not_leave_stale_conversion(self):
        job=capture.create_job("test.rdc"); capture.update(job["id"],status="replaying")
        self.assertEqual(capture.load_job(job["id"])["status"],"interrupted")
    def test_upload_queues_real_background_job_without_model(self):
        class Uploaded:
            async def stream(self):
                yield b"RDOC-test"
        def converted(job_id):
            capture.update(job_id,status="done",overview={"draw_calls":3})
        async def run():
            with patch.object(capture,"convert",side_effect=converted):
                job=await api.upload(Uploaded(),"test.rdc","")
                await asyncio.gather(*list(capture._TASKS.values()))
                return capture.load_job(job["id"])
        job=asyncio.run(run())
        self.assertEqual(job["status"],"done")
        self.assertEqual((self.root/job["id"]/"capture.rdc").read_bytes(),b"RDOC-test")
if __name__=="__main__": unittest.main()
