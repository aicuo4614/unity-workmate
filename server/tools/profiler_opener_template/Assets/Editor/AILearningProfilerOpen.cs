// 由 AILearning 服务端拉起（旁路小工程）：
// - mode=open：LoadProfile 进 Profiler 窗口；frame>=0 时把光标移到该帧
// - mode=convert：Load → SaveProfile(.data) → 写全帧 .detaildata → result → Exit
// .aidata 不在这里写：服务端从 .detaildata 裁摘要，对话才吃那份小文件。
// 为何不在业务工程塞脚本：版本用 ProjectVersion.txt 对齐，且可 -executeMethod 无人值守。
// 已开着的 Editor 不会再次跑 executeMethod，所以 open 模式会盯 jump_frame.json。
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditor.Profiling;
using UnityEditorInternal;
using UnityEngine;

namespace AILearning
{
    // 没有这个属性，工程已经开着时静态构造不会跑，转换请求没人收。
    [InitializeOnLoad]
    public static class ProfilerOpen
    {
        const string PendingName = "open_pending.json";
        const string JumpName = "jump_frame.json";
        const string ConvertName = "convert_request.json";
        const int PerFrameCpuLimit = 12;
        const int PerFrameWaitLimit = 8;
        const int PerFrameCallsLimit = 12;
        const int PerFrameGcLimit = 12;

        [Serializable]
        class Pending
        {
            public string mode;
            public string raw_path;
            public string data_path;
            public string detaildata_path;
            public string aidata_path;
            public string result_path;
            public string unity_version;
            public string editor_version;
            // <0 表示只打开、不挪光标。JsonUtility 缺字段会变成 0，服务端总会写上这个字段。
            public int frame = -1;
        }

        [Serializable]
        class JumpReq
        {
            public string raw_path;
            public int frame = -1;
            public string nonce;
        }

        static Pending _pending;
        static bool _busy;
        static int _waitTicks;
        static bool _convertStarted;
        static int _revealTicks;
        static int _jumpPollTicks;
        static int _openJumpFrame = -1;
        static string _loadedRaw = "";
        // 字符串：JsonUtility 把 long 读成 0，已开着的窗口会把定位请求当成空文件丢掉。
        static string _seenJumpNonce = "";
        static bool _jumpWaiting;
        // 冷启动的 convert 做完要退出；工程已经开着时不能退，否则把人正在看的 Profiler 关掉。
        static bool _quitAfterConvert;
        // 用时间而不是 update 次数：大 .raw 加载时编辑器一帧可能很密，3600 下不到一分钟就超时。
        static double _convertDeadline;
        // 同一份 .raw 里计数器 marker id 不变；换一次转换要清掉，避免沿用上一份。
        static int _cBatches = int.MinValue;
        static int _cDrawCalls = int.MinValue;
        static int _cSetPass = int.MinValue;
        static int _cTris = int.MinValue;
        static int _cVerts = int.MinValue;
        static int _cShadows = int.MinValue;
        static int _cInstanced = int.MinValue;

        [Serializable]
        class ConvertReq
        {
            public string mode;
            public string raw_path;
            public string data_path;
            public string detaildata_path;
            public string aidata_path;
            public string result_path;
            public string unity_version;
            public string editor_version;
            public int frame = -1;
            // 字符串：JsonUtility 把 long 读成 0 时，请求会被当成空文件直接丢掉。
            public string nonce;
        }

        // 旁路工程常驻后，第二次启动不会跑 executeMethod，只能靠 update 收请求。
        static ProfilerOpen()
        {
            EditorApplication.update -= PollJump;
            EditorApplication.update += PollJump;
        }

        public static void RunPending()
        {
            if (_busy)
                return;
            _busy = true;
            _convertStarted = false;
            _waitTicks = 0;

            string projectRoot = Directory.GetParent(Application.dataPath)?.FullName ?? "";
            string pendingPath = Path.Combine(projectRoot, PendingName);
            if (!File.Exists(pendingPath))
            {
                FailAndMaybeQuit(null, "缺少 open_pending.json");
                return;
            }

            try
            {
                _pending = JsonUtility.FromJson<Pending>(File.ReadAllText(pendingPath));
            }
            catch (Exception e)
            {
                FailAndMaybeQuit(null, "解析 open_pending.json 失败: " + e.Message);
                return;
            }

            if (_pending == null || string.IsNullOrEmpty(_pending.raw_path) || !File.Exists(_pending.raw_path))
            {
                FailAndMaybeQuit(_pending, "找不到 .raw：" + (_pending != null ? _pending.raw_path : ""));
                return;
            }

            string mode = string.IsNullOrEmpty(_pending.mode) ? "open" : _pending.mode;
            if (mode == "convert")
            {
                _quitAfterConvert = true;
                StartConvert();
            }
            else
            {
                _quitAfterConvert = false;
                StartOpen();
            }
        }

        // 兼容旧入口
        public static void LoadPending()
        {
            RunPending();
        }

        static void StartOpen()
        {
            ShowProfilerWindow();
            _openJumpFrame = _pending != null ? _pending.frame : -1;
            _revealTicks = 0;
            _jumpWaiting = true;
            _loadedRaw = _pending != null ? _pending.raw_path : "";
            EditorApplication.update -= WaitFramesThenReveal;
            EditorApplication.update += WaitFramesThenReveal;
            ProfilerDriver.profileLoaded -= OnProfileLoadedForOpen;
            ProfilerDriver.profileLoaded += OnProfileLoadedForOpen;

            if (!ProfilerDriver.LoadProfile(_pending.raw_path, false))
            {
                EditorApplication.update -= WaitFramesThenReveal;
                ProfilerDriver.profileLoaded -= OnProfileLoadedForOpen;
                _jumpWaiting = false;
                EditorUtility.DisplayDialog(
                    "AILearning Profiler",
                    "LoadProfile 失败。常见原因：Editor 与打真机包的 Unity 大版本不一致。\n" + _pending.raw_path,
                    "OK");
                _busy = false;
                return;
            }

            Debug.Log("[AILearning] Profiler 开始加载: " + _pending.raw_path);
        }

        static void OnProfileLoadedForOpen()
        {
            _revealTicks = Math.Max(_revealTicks, 1);
        }

        static void ShowProfilerWindow()
        {
            try
            {
                var window = EditorWindow.GetWindow<ProfilerWindow>();
                window.Show();
                window.Focus();
            }
            catch (Exception e)
            {
                Debug.LogWarning("[AILearning] 打开 ProfilerWindow 失败: " + e.Message);
            }
        }

        // LoadProfile 异步，帧索引晚几拍才稳定；定位必须等帧就绪。
        static void WaitFramesThenReveal()
        {
            if (!_jumpWaiting)
            {
                EditorApplication.update -= WaitFramesThenReveal;
                return;
            }

            _revealTicks++;
            int frames = CountFrames();
            if (frames > 0 && _revealTicks >= 3)
            {
                EditorApplication.update -= WaitFramesThenReveal;
                ProfilerDriver.profileLoaded -= OnProfileLoadedForOpen;
                _jumpWaiting = false;
                _revealTicks = 0;
                SelectFrame(_openJumpFrame);
                // 这次启动对应的 jump 文件已经消费，避免轮询再跳一次
                _seenJumpNonce = CurrentJumpNonce();
                _jumpPollTicks = 0;
                EditorApplication.update -= PollJump;
                EditorApplication.update += PollJump;
                _busy = false;
                return;
            }

            if (_revealTicks > 3600)
            {
                EditorApplication.update -= WaitFramesThenReveal;
                ProfilerDriver.profileLoaded -= OnProfileLoadedForOpen;
                _jumpWaiting = false;
                _busy = false;
                Debug.LogWarning("[AILearning] 等待 Profiler 帧超时，无法定位光标");
            }
        }

        // 工程已开着时第二次启动不会重跑 executeMethod，只能靠这个文件把光标挪过去。
        static void PollJump()
        {
            // 转换进行中不要再 Load 别的录制，否则 .detaildata 会对错帧。
            // _busy 不能挡在前面：Profiler 窗口开着等帧时也是 busy，转换请求会一直没人收。
            if (_convertStarted)
                return;
            if (TryConsumeConvertRequest())
                return;
            if (_busy)
                return;

            if (_jumpWaiting)
            {
                _revealTicks++;
                if (CountFrames() > 0 && _revealTicks >= 3)
                {
                    _jumpWaiting = false;
                    _revealTicks = 0;
                    ProfilerDriver.profileLoaded -= OnProfileLoadedForOpen;
                    SelectFrame(_openJumpFrame);
                }
                else if (_revealTicks > 3600)
                {
                    _jumpWaiting = false;
                    _revealTicks = 0;
                    ProfilerDriver.profileLoaded -= OnProfileLoadedForOpen;
                    Debug.LogWarning("[AILearning] 换录制后等待帧超时");
                }
                return;
            }

            _jumpPollTicks++;
            if (_jumpPollTicks < 30)
                return;
            _jumpPollTicks = 0;

            JumpReq jump = ReadJump();
            if (jump == null || string.IsNullOrEmpty(jump.nonce) || jump.nonce == _seenJumpNonce || jump.frame < 0)
                return;
            _seenJumpNonce = jump.nonce;

            if (!string.IsNullOrEmpty(jump.raw_path) && !SameRaw(jump.raw_path, _loadedRaw))
            {
                if (!File.Exists(jump.raw_path))
                {
                    Debug.LogWarning("[AILearning] 定位目标 .raw 不存在: " + jump.raw_path);
                    return;
                }
                _loadedRaw = jump.raw_path;
                _openJumpFrame = jump.frame;
                _revealTicks = 0;
                _jumpWaiting = true;
                ProfilerDriver.profileLoaded -= OnProfileLoadedForOpen;
                ProfilerDriver.profileLoaded += OnProfileLoadedForOpen;
                if (!ProfilerDriver.LoadProfile(jump.raw_path, false))
                {
                    _jumpWaiting = false;
                    ProfilerDriver.profileLoaded -= OnProfileLoadedForOpen;
                    Debug.LogWarning("[AILearning] 换录制 LoadProfile 失败: " + jump.raw_path);
                }
                return;
            }

            SelectFrame(jump.frame);
        }

        // 文件删掉再开跑：域重载会把内存里的 nonce 清掉，留下文件就会转第二次。
        static bool TryConsumeConvertRequest()
        {
            string path = Path.Combine(ProjectRoot(), ConvertName);
            if (!File.Exists(path))
                return false;
            ConvertReq req;
            try
            {
                req = JsonUtility.FromJson<ConvertReq>(File.ReadAllText(path));
            }
            catch
            {
                return false;
            }
            if (req == null || string.IsNullOrEmpty(req.nonce) || string.IsNullOrEmpty(req.raw_path) || !File.Exists(req.raw_path))
                return false;
            try
            {
                File.Delete(path);
            }
            catch (Exception e)
            {
                Debug.LogWarning("[AILearning] 删 convert_request 失败: " + e.Message);
                return false;
            }

            // 人正在看的那次打开让路，否则两个 LoadProfile 抢同一份窗口。
            EditorApplication.update -= WaitFramesThenReveal;
            ProfilerDriver.profileLoaded -= OnProfileLoadedForOpen;
            _jumpWaiting = false;
            _quitAfterConvert = false;
            _convertStarted = false;
            _waitTicks = 0;
            _busy = true;
            _pending = new Pending
            {
                mode = "convert",
                raw_path = req.raw_path,
                data_path = req.data_path,
                detaildata_path = req.detaildata_path,
                aidata_path = req.aidata_path,
                result_path = req.result_path,
                unity_version = req.unity_version,
                editor_version = req.editor_version,
                frame = -1,
            };
            Debug.Log("[AILearning] 工程已打开，在当前窗口转换: " + req.raw_path);
            StartConvert();
            return true;
        }

        static void SelectFrame(int frame)
        {
            if (frame < 0)
                return;
            int first = ProfilerDriver.firstFrameIndex;
            int last = ProfilerDriver.lastFrameIndex;
            if (last < first)
                return;
            if (frame < first)
                frame = first;
            if (frame > last)
                frame = last;
            try
            {
                var window = EditorWindow.GetWindow<ProfilerWindow>();
                window.Show();
                window.Focus();
                // 2022.3 的光标在 ProfilerWindow 上，ProfilerDriver 没有 SetSelectedFrameIndex。
                window.selectedFrameIndex = frame;
                Debug.Log("[AILearning] Profiler 光标 → 帧 " + frame);
            }
            catch (Exception e)
            {
                Debug.LogWarning("[AILearning] 定位帧失败: " + e.Message);
            }
        }

        static string ProjectRoot()
        {
            return Directory.GetParent(Application.dataPath)?.FullName ?? "";
        }

        static JumpReq ReadJump()
        {
            try
            {
                string path = Path.Combine(ProjectRoot(), JumpName);
                if (!File.Exists(path))
                    return null;
                return JsonUtility.FromJson<JumpReq>(File.ReadAllText(path));
            }
            catch
            {
                return null;
            }
        }

        static string CurrentJumpNonce()
        {
            JumpReq jump = ReadJump();
            return jump != null && jump.nonce != null ? jump.nonce : "";
        }

        static bool SameRaw(string a, string b)
        {
            if (string.IsNullOrEmpty(a) || string.IsNullOrEmpty(b))
                return false;
            try
            {
                return string.Equals(
                    Path.GetFullPath(a),
                    Path.GetFullPath(b),
                    StringComparison.OrdinalIgnoreCase);
            }
            catch
            {
                return string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
            }
        }

        static double ConvertWaitBudget(string rawPath)
        {
            try
            {
                long bytes = new FileInfo(rawPath).Length;
                // 按大约 4MB/s 估加载。1.6GB 不能用固定几十秒，帧索引出来得晚。
                double sec = bytes / (4.0 * 1024 * 1024);
                if (sec < 180)
                    sec = 180;
                if (sec > 1500)
                    sec = 1500;
                return sec;
            }
            catch
            {
                return 600;
            }
        }

        static void StartConvert()
        {
            // Load 可能异步；等 profileLoaded，并用 update 轮询帧就绪（防空 Save）
            _convertDeadline = EditorApplication.timeSinceStartup + ConvertWaitBudget(
                _pending != null ? _pending.raw_path : "");
            ProfilerDriver.profileLoaded -= OnProfileLoadedForConvert;
            ProfilerDriver.profileLoaded += OnProfileLoadedForConvert;
            EditorApplication.update -= WaitFramesThenConvert;
            EditorApplication.update += WaitFramesThenConvert;

            if (!ProfilerDriver.LoadProfile(_pending.raw_path, false))
            {
                ProfilerDriver.profileLoaded -= OnProfileLoadedForConvert;
                EditorApplication.update -= WaitFramesThenConvert;
                FailAndMaybeQuit(_pending, "LoadProfile 失败（版本不匹配？）: " + _pending.raw_path);
                return;
            }
        }

        static void OnProfileLoadedForConvert()
        {
            // 事件到了仍可能帧索引未刷新，交给轮询统一收口
            _waitTicks = Math.Max(_waitTicks, 1);
        }

        static void WaitFramesThenConvert()
        {
            if (_pending == null || _pending.mode != "convert" || _convertStarted)
            {
                EditorApplication.update -= WaitFramesThenConvert;
                return;
            }

            _waitTicks++;
            int frames = CountFrames();
            // ~30s @60Hz update；大 raw 加载再放宽
            if (frames > 0 && _waitTicks >= 3)
            {
                FinishConvert("framesReady:" + frames);
                return;
            }
            if (EditorApplication.timeSinceStartup > _convertDeadline)
            {
                EditorApplication.update -= WaitFramesThenConvert;
                ProfilerDriver.profileLoaded -= OnProfileLoadedForConvert;
                FailAndMaybeQuit(_pending, "等待 Profiler 帧超时（Load 后无帧）");
            }
        }

        static void FinishConvert(string via)
        {
            if (_pending == null || _pending.mode != "convert" || _convertStarted)
                return;
            _convertStarted = true;
            var pending = _pending;
            _pending = null;
            ProfilerDriver.profileLoaded -= OnProfileLoadedForConvert;
            EditorApplication.update -= WaitFramesThenConvert;

            try
            {
                Debug.Log("[AILearning] convert via " + via + " raw=" + pending.raw_path);

                if (string.IsNullOrEmpty(pending.data_path))
                    pending.data_path = Path.ChangeExtension(pending.raw_path, ".data");
                if (string.IsNullOrEmpty(pending.detaildata_path))
                    pending.detaildata_path = Path.ChangeExtension(pending.raw_path, ".detaildata");
                if (string.IsNullOrEmpty(pending.result_path))
                    pending.result_path = pending.detaildata_path + ".result.json";

                Directory.CreateDirectory(Path.GetDirectoryName(pending.data_path) ?? ".");
                Directory.CreateDirectory(Path.GetDirectoryName(pending.detaildata_path) ?? ".");

                ProfilerDriver.SaveProfile(pending.data_path);

                var detail = BuildDetaildata(pending);
                File.WriteAllText(pending.detaildata_path, detail, new UTF8Encoding(false));

                WriteResult(pending.result_path, true, "", pending, CountFrames());
                Debug.Log("[AILearning] convert ok data=" + pending.data_path + " detail=" + pending.detaildata_path);
            }
            catch (Exception e)
            {
                Debug.LogError("[AILearning] convert 失败: " + e);
                WriteResult(
                    pending.result_path,
                    false,
                    e.Message,
                    pending,
                    0);
            }
            finally
            {
                _busy = false;
                _convertStarted = false;
                if (_quitAfterConvert)
                    EditorApplication.delayCall += () => EditorApplication.Exit(0);
            }
        }

        static int CountFrames()
        {
            int first = ProfilerDriver.firstFrameIndex;
            int last = ProfilerDriver.lastFrameIndex;
            if (last < first)
                return 0;
            return last - first + 1;
        }

        // container=套娃根节点不进主 Top；wait=阻塞/Present；work=可行动业务/渲染
        enum MarkerBucket { Container, Wait, Work }

        static MarkerBucket ClassifyMarker(string name)
        {
            if (string.IsNullOrEmpty(name))
                return MarkerBucket.Container;
            // 整帧容器：占位大、无行动价值
            if (name == "Main Thread"
                || name == "PlayerLoop"
                || name == "EditorLoop"
                || name == "Profiler.Default"
                || name == "Overhead")
                return MarkerBucket.Container;

            // 等待 / Present / 限帧（对话应单独叙事，勿压过业务 Top）
            if (name.IndexOf("WaitForPresent", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("WaitForLastPresent", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("Gfx.WaitForPresent", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("Semaphore.Wait", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("WaitForTargetFPS", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("WaitForLastPresentation", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("WaitForJobGroupID", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("Gfx.WaitFor", StringComparison.OrdinalIgnoreCase) >= 0
                || name.StartsWith("WaitFor", StringComparison.Ordinal)
                || name.IndexOf("PresentWait", StringComparison.OrdinalIgnoreCase) >= 0)
                return MarkerBucket.Wait;

            return MarkerBucket.Work;
        }

        static List<KeyValuePair<string, double>> TopOf(
            Dictionary<string, double> totals, int limit)
        {
            var list = new List<KeyValuePair<string, double>>(totals);
            list.Sort((a, b) => b.Value.CompareTo(a.Value));
            if (list.Count > limit)
                list = list.GetRange(0, limit);
            return list;
        }

        static List<KeyValuePair<string, int>> TopOfInt(
            Dictionary<string, int> totals, int limit)
        {
            var list = new List<KeyValuePair<string, int>>(totals);
            list.Sort((a, b) => b.Value.CompareTo(a.Value));
            if (list.Count > limit)
                list = list.GetRange(0, limit);
            return list;
        }

        static List<KeyValuePair<string, long>> TopOfLong(
            Dictionary<string, long> totals, int limit)
        {
            var list = new List<KeyValuePair<string, long>>(totals);
            list.Sort((a, b) => b.Value.CompareTo(a.Value));
            if (list.Count > limit)
                list = list.GetRange(0, limit);
            return list;
        }

        static void AppendMarkerArray(
            StringBuilder sb,
            string indent,
            List<KeyValuePair<string, double>> markers,
            Dictionary<string, int> hitsMap,
            string msKey = "total_ms")
        {
            sb.Append("[\n");
            for (int i = 0; i < markers.Count; i++)
            {
                var kv = markers[i];
                int hits = hitsMap.ContainsKey(kv.Key) ? hitsMap[kv.Key] : 0;
                sb.Append(indent).Append("  {\"name\": \"").Append(Esc(kv.Key))
                    .Append("\", \"").Append(msKey).Append("\": ").Append(F(kv.Value))
                    .Append(", \"hits\": ").Append(hits).Append("}");
                sb.Append(i + 1 < markers.Count ? ",\n" : "\n");
            }
            sb.Append(indent).Append("]");
        }

        /// <summary>
        /// GPU Usage 模块的 frameGpuTimeMs 在 Mali 上是 0。
        /// 插件把 Frame Timing 的 GPU Frame Time（纳秒）录进 .raw，这里换算成毫秒。
        /// </summary>
        static float ReadGpuFrameMs(RawFrameDataView view)
        {
            float moduleMs = view.frameGpuTimeMs;
            if (moduleMs > 0.05f)
                return moduleMs;
            int id = view.GetMarkerId("GPU Frame Time");
            if (id == FrameDataView.invalidMarkerId)
                return 0f;
            long raw = 0;
            try
            {
                raw = view.GetCounterValueAsLong(id);
            }
            catch
            {
                return 0f;
            }
            if (raw <= 0)
                return 0f;
            return (float)(raw / 1e6);
        }

        struct FrameRenderStats
        {
            public long batches;
            public long drawCalls;
            public long setpass;
            public long triangles;
            public long vertices;
            public long shadowCasters;
            public long instancedBatches;
            public float postMs;
        }

        static void ResetRenderCounterCache()
        {
            _cBatches = int.MinValue;
            _cDrawCalls = int.MinValue;
            _cSetPass = int.MinValue;
            _cTris = int.MinValue;
            _cVerts = int.MinValue;
            _cShadows = int.MinValue;
            _cInstanced = int.MinValue;
        }

        static FrameRenderStats EmptyRenderStats()
        {
            return new FrameRenderStats
            {
                batches = -1,
                drawCalls = -1,
                setpass = -1,
                triangles = -1,
                vertices = -1,
                shadowCasters = -1,
                instancedBatches = -1,
                postMs = 0f,
            };
        }

        // Rendering 模块的帧计数。名字是 2022.3 Profiler 计数器，不是 GPU Usage 那棵树。
        static long ReadCounter(RawFrameDataView view, ref int cache, string name)
        {
            if (cache == int.MinValue)
                cache = view.GetMarkerId(name);
            if (cache == FrameDataView.invalidMarkerId)
                return -1;
            try
            {
                return view.GetCounterValueAsLong(cache);
            }
            catch
            {
                return -1;
            }
        }

        static bool IsPostMarker(string name)
        {
            if (string.IsNullOrEmpty(name))
                return false;
            return name.IndexOf("PostProcess", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("UberPost", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("ColorGrading", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("MotionBlur", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("DepthOfField", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("Tonemap", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("AmbientOcclusion", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        static FrameRenderStats ReadRenderStats(RawFrameDataView view, float postMs)
        {
            var stats = EmptyRenderStats();
            stats.batches = ReadCounter(view, ref _cBatches, "Batches Count");
            // 与 Batches 并列：Batches 是合批后，Draw Calls 是实际提交次数
            stats.drawCalls = ReadCounter(view, ref _cDrawCalls, "Draw Calls Count");
            stats.setpass = ReadCounter(view, ref _cSetPass, "SetPass Calls Count");
            stats.triangles = ReadCounter(view, ref _cTris, "Triangles Count");
            stats.vertices = ReadCounter(view, ref _cVerts, "Vertices Count");
            stats.shadowCasters = ReadCounter(view, ref _cShadows, "Shadow Casters Count");
            stats.instancedBatches = ReadCounter(view, ref _cInstanced, "Instanced Batches Count");
            stats.postMs = postMs;
            return stats;
        }

        static void AppendRenderStats(StringBuilder sb, FrameRenderStats stats)
        {
            sb.Append(",\"batches\":").Append(stats.batches);
            sb.Append(",\"draw_calls\":").Append(stats.drawCalls);
            sb.Append(",\"setpass\":").Append(stats.setpass);
            sb.Append(",\"triangles\":").Append(stats.triangles);
            sb.Append(",\"vertices\":").Append(stats.vertices);
            sb.Append(",\"shadow_casters\":").Append(stats.shadowCasters);
            sb.Append(",\"instanced_batches\":").Append(stats.instancedBatches);
            sb.Append(",\"post_ms\":").Append(F(stats.postMs));
        }

        // Time：同名嵌套只加最外层（外层 Time 已含子调用）。Self：每层自己的时间，可以相加。
        // Calls：每次出现都 +1（含嵌套），对齐 Profiler Hierarchy 的 Calls。
        class MarkerBag
        {
            public readonly Dictionary<string, double> time = new Dictionary<string, double>();
            public readonly Dictionary<string, int> timeHits = new Dictionary<string, int>();
            public readonly Dictionary<string, double> self = new Dictionary<string, double>();
            public readonly Dictionary<string, int> selfHits = new Dictionary<string, int>();
            public readonly Dictionary<string, int> calls = new Dictionary<string, int>();

            public void Add(string name, bool nestedSameName, float inclusiveMs, double selfMs)
            {
                if (!nestedSameName && inclusiveMs > 0.01f)
                    AddTo(time, timeHits, name, inclusiveMs);
                if (selfMs > 0.01)
                    AddTo(self, selfHits, name, selfMs);
                if (!calls.ContainsKey(name))
                    calls[name] = 0;
                calls[name] += 1;
            }

            public double SumSelf()
            {
                double sum = 0;
                foreach (var kv in self)
                    sum += kv.Value;
                return sum;
            }

            public int SumCalls()
            {
                int sum = 0;
                foreach (var kv in calls)
                    sum += kv.Value;
                return sum;
            }

            static void AddTo(Dictionary<string, double> totals, Dictionary<string, int> hits, string name, double ms)
            {
                if (!totals.ContainsKey(name))
                {
                    totals[name] = 0;
                    hits[name] = 0;
                }
                totals[name] += ms;
                hits[name] += 1;
            }
        }

        class GcBag
        {
            public readonly Dictionary<string, long> bytes = new Dictionary<string, long>();
            public readonly Dictionary<string, int> hits = new Dictionary<string, int>();
            public long total;

            public void Add(string name, long amount)
            {
                if (amount <= 0 || string.IsNullOrEmpty(name))
                    return;
                if (!bytes.ContainsKey(name))
                {
                    bytes[name] = 0;
                    hits[name] = 0;
                }
                bytes[name] += amount;
                hits[name] += 1;
                total += amount;
            }
        }

        /// <summary>扫一帧主线程调用树。Time 用 GetSampleTimeMs（含子调用），Self 扣掉直接子 sample。</summary>
        static float CollectFrameMarkers(
            int frameIndex,
            MarkerBag work,
            MarkerBag wait,
            GcBag gc,
            HashSet<string> skippedContainers,
            ref double gcAllocMs,
            ref int gcAllocHits,
            out float gpuMs,
            out FrameRenderStats renderStats,
            string callTreePath)
        {
            if (File.Exists(callTreePath)) File.Delete(callTreePath);
            float frameMs = 0f;
            gpuMs = 0f;
            renderStats = EmptyRenderStats();
            try
            {
                using (var view = ProfilerDriver.GetRawFrameDataView(frameIndex, 0))
                {
                    if (view == null || !view.valid)
                        return 0f;
                    frameMs = view.frameTimeMs;
                    gpuMs = ReadGpuFrameMs(view);
                    // 不能截断：半截子树会把没扫到的子调用算进父 sample 的 Self。
                    // 用显式栈而不是递归：深 Profile 一层方法一个 sample，递归会撑爆调用栈。
                    int limit = view.sampleCount;
                    float postMs = 0f;
                    var open = new List<OpenSample>();
                    var nameDepth = new Dictionary<string, int>();
                    for (int i = 0; i < limit; i++)
                    {
                        while (open.Count > 0 && i >= open[open.Count - 1].end)
                            CloseSample(open, nameDepth, work, wait, skippedContainers, ref gcAllocMs, ref gcAllocHits, ref postMs);

                        string name = view.GetSampleName(i) ?? "";
                        // GC.Alloc 的字节挂在父函数上，对齐 Hierarchy 的 GC Alloc 列。
                        if (IsGcAllocMarker(name))
                        {
                            long bytes = ReadGcAllocBytes(view, i);
                            if (bytes > 0)
                            {
                                string owner = FindGcOwner(open);
                                if (!string.IsNullOrEmpty(owner))
                                    gc.Add(owner, bytes);
                                else
                                    gc.Add(name, bytes);
                            }
                        }
                        float inclusive = view.GetSampleTimeMs(i);
                        int span = 0;
                        try
                        {
                            span = view.GetSampleChildrenCountRecursive(i);
                        }
                        catch
                        {
                            span = 0;
                        }
                        int end = i + 1 + Math.Max(0, span);
                        if (end <= i)
                            end = i + 1;
                        if (end > limit)
                            end = limit;

                        int seen = 0;
                        if (!string.IsNullOrEmpty(name))
                            nameDepth.TryGetValue(name, out seen);
                        if (open.Count > 0)
                            open[open.Count - 1].childSum += inclusive;
                        open.Add(new OpenSample
                        {
                            end = end,
                            inclusive = inclusive,
                            childSum = 0,
                            name = name,
                            nestedSame = seen > 0,
                        });
                        if (!string.IsNullOrEmpty(name))
                            nameDepth[name] = seen + 1;
                    }
                    while (open.Count > 0)
                        CloseSample(open, nameDepth, work, wait, skippedContainers, ref gcAllocMs, ref gcAllocHits, ref postMs);
                    renderStats = ReadRenderStats(view, postMs);
                    WriteCallTree(view, frameIndex, callTreePath);
                }
            }
            catch
            {
                /* 单帧失败跳过 */
            }
            return frameMs;
        }

        class CallNode
        {
            public int parent;
            public string name;
            public int hits;
            public double total, self;
            public long bytes;
            public Dictionary<string, int> children = new Dictionary<string, int>();
        }

        // 按完整父路径合并同名 sample；保留容器节点，以免调用链断层。用平表避免深调用栈序列化溢出。
        static void WriteCallTree(RawFrameDataView view, int frame, string path)
        {
            var nodes = new List<CallNode>();
            var roots = new Dictionary<string, int>();
            var stack = new List<int>();
            var ends = new List<int>();
            for (int i = 0; i < view.sampleCount; i++)
            {
                while (ends.Count > 0 && i >= ends[ends.Count - 1])
                {
                    ends.RemoveAt(ends.Count - 1);
                    stack.RemoveAt(stack.Count - 1);
                }
                string name = view.GetSampleName(i) ?? "";
                int parent = stack.Count > 0 ? stack[stack.Count - 1] : -1;
                var siblings = parent >= 0 ? nodes[parent].children : roots;
                int index;
                if (!siblings.TryGetValue(name, out index))
                {
                    index = nodes.Count;
                    nodes.Add(new CallNode { parent = parent, name = name });
                    siblings[name] = index;
                }
                var node = nodes[index];
                double ms = view.GetSampleTimeMs(i);
                node.hits++;
                node.total += ms;
                node.self += ms;
                if (parent >= 0) nodes[parent].self -= ms;
                if (IsGcAllocMarker(name))
                {
                    long bytes = Math.Max(0, ReadGcAllocBytes(view, i));
                    // 与排行榜保持同一归属口径：略过容器，把分配记到最近业务父节点。
                    int owner = index;
                    for (int j = stack.Count - 1; j >= 0; j--)
                    {
                        var ancestor = nodes[stack[j]];
                        if (string.IsNullOrEmpty(ancestor.name) || IsGcAllocMarker(ancestor.name)
                            || ClassifyMarker(ancestor.name) == MarkerBucket.Container) continue;
                        owner = stack[j];
                        break;
                    }
                    nodes[owner].bytes += bytes;
                }
                stack.Add(index);
                ends.Add(Math.Min(view.sampleCount, i + 1 + Math.Max(0, view.GetSampleChildrenCountRecursive(i))));
            }
            var sb = new StringBuilder();
            sb.Append("{\"schema\":\"ailearning.profiler.calls/v1\",\"thread\":\"Main Thread\",\"frame\":").Append(frame);
            sb.Append(",\"nodes\":[");
            for (int i = 0; i < nodes.Count; i++)
            {
                if (i > 0) sb.Append(',');
                var node = nodes[i];
                sb.Append("{\"parent\":").Append(node.parent);
                sb.Append(",\"name\":\"").Append(Esc(node.name)).Append('"');
                sb.Append(",\"hits\":").Append(node.hits);
                sb.Append(",\"total_ms\":").Append(F(node.total));
                sb.Append(",\"self_ms\":").Append(F(Math.Max(0, node.self)));
                sb.Append(",\"bytes\":").Append(node.bytes).Append('}');
            }
            sb.Append("]}");
            string temp = path + ".tmp";
            File.WriteAllText(temp, sb.ToString(), new UTF8Encoding(false));
            if (File.Exists(path)) File.Delete(path);
            File.Move(temp, path);
        }

        static bool IsGcAllocMarker(string name)
        {
            return name == "GC.Alloc" || name.EndsWith("GC.Alloc", StringComparison.Ordinal);
        }

        static long ReadGcAllocBytes(RawFrameDataView view, int sampleIndex)
        {
            try
            {
                if (view.GetSampleMetadataCount(sampleIndex) <= 0)
                    return 0;
                return view.GetSampleMetadataAsLong(sampleIndex, 0);
            }
            catch
            {
                return 0;
            }
        }

        // 从调用栈往上找第一个非容器、非 GC.Alloc 的父 sample。
        static string FindGcOwner(List<OpenSample> open)
        {
            for (int i = open.Count - 1; i >= 0; i--)
            {
                string name = open[i].name;
                if (string.IsNullOrEmpty(name) || IsGcAllocMarker(name))
                    continue;
                if (ClassifyMarker(name) == MarkerBucket.Container)
                    continue;
                return name;
            }
            return "";
        }

        class OpenSample
        {
            public int end;
            public float inclusive;
            public double childSum;
            public string name;
            public bool nestedSame;
        }

        static void CloseSample(
            List<OpenSample> open,
            Dictionary<string, int> nameDepth,
            MarkerBag work,
            MarkerBag wait,
            HashSet<string> skippedContainers,
            ref double gcAllocMs,
            ref int gcAllocHits,
            ref float postMs)
        {
            var done = open[open.Count - 1];
            open.RemoveAt(open.Count - 1);
            if (string.IsNullOrEmpty(done.name))
                return;
            int seen;
            if (nameDepth.TryGetValue(done.name, out seen))
            {
                if (seen <= 1)
                    nameDepth.Remove(done.name);
                else
                    nameDepth[done.name] = seen - 1;
            }

            // GC.Alloc 本身不进 CPU/Calls Top；字节已经记到父函数。
            if (IsGcAllocMarker(done.name))
                return;

            double selfMs = done.inclusive - done.childSum;
            if (selfMs < 0)
                selfMs = 0;
            var bucket = ClassifyMarker(done.name);
            if (bucket == MarkerBucket.Container)
            {
                skippedContainers.Add(done.name);
                return;
            }
            var bag = bucket == MarkerBucket.Wait ? wait : work;
            bag.Add(done.name, done.nestedSame, done.inclusive, selfMs);
            if (bucket == MarkerBucket.Work && IsPostMarker(done.name) && !done.nestedSame && done.inclusive > postMs)
                postMs = done.inclusive;
            if (selfMs > 0.01 && IsGcMarker(done.name))
            {
                gcAllocMs += selfMs;
                gcAllocHits++;
            }
        }

        static bool IsGcMarker(string name)
        {
            return name.IndexOf("GC.Alloc", StringComparison.Ordinal) >= 0
                || name == "GC.Collect"
                || name.IndexOf("GarbageCollect", StringComparison.Ordinal) >= 0;
        }

        static void MergeTotals(
            Dictionary<string, double> intoT,
            Dictionary<string, int> intoH,
            Dictionary<string, double> fromT,
            Dictionary<string, int> fromH)
        {
            foreach (var kv in fromT)
            {
                if (!intoT.ContainsKey(kv.Key))
                {
                    intoT[kv.Key] = 0;
                    intoH[kv.Key] = 0;
                }
                intoT[kv.Key] += kv.Value;
                intoH[kv.Key] += fromH.ContainsKey(kv.Key) ? fromH[kv.Key] : 0;
            }
        }

        static void AppendCompactMarkers(
            StringBuilder sb,
            List<KeyValuePair<string, double>> markers,
            Dictionary<string, int> hitsMap)
        {
            sb.Append("[");
            for (int i = 0; i < markers.Count; i++)
            {
                var kv = markers[i];
                int hits = hitsMap.ContainsKey(kv.Key) ? hitsMap[kv.Key] : 0;
                if (i > 0)
                    sb.Append(",");
                sb.Append("{\"name\":\"").Append(Esc(kv.Key))
                    .Append("\",\"ms\":").Append(F(kv.Value))
                    .Append(",\"hits\":").Append(hits).Append("}");
            }
            sb.Append("]");
        }

        static void AppendCompactCalls(
            StringBuilder sb,
            List<KeyValuePair<string, int>> markers,
            Dictionary<string, double> selfMs)
        {
            sb.Append("[");
            for (int i = 0; i < markers.Count; i++)
            {
                var kv = markers[i];
                double ms = selfMs != null && selfMs.ContainsKey(kv.Key) ? selfMs[kv.Key] : 0;
                if (i > 0)
                    sb.Append(",");
                sb.Append("{\"name\":\"").Append(Esc(kv.Key))
                    .Append("\",\"hits\":").Append(kv.Value)
                    .Append(",\"ms\":").Append(F(ms)).Append("}");
            }
            sb.Append("]");
        }

        static void AppendCompactGc(
            StringBuilder sb,
            List<KeyValuePair<string, long>> markers,
            Dictionary<string, int> hitsMap)
        {
            sb.Append("[");
            for (int i = 0; i < markers.Count; i++)
            {
                var kv = markers[i];
                int hits = hitsMap != null && hitsMap.ContainsKey(kv.Key) ? hitsMap[kv.Key] : 0;
                if (i > 0)
                    sb.Append(",");
                sb.Append("{\"name\":\"").Append(Esc(kv.Key))
                    .Append("\",\"bytes\":").Append(kv.Value)
                    .Append(",\"hits\":").Append(hits).Append("}");
            }
            sb.Append("]");
        }

        /// <summary>
        /// 全帧扫一遍：详情要每一帧的 Top，不能再抽样。
        /// 每帧只留 CPU 12 + 等待 8，避免把 .raw 的 sample 树原样写成 JSON。
        /// </summary>
        static string BuildDetaildata(Pending pending)
        {
            int first = ProfilerDriver.firstFrameIndex;
            int last = ProfilerDriver.lastFrameIndex;
            var cpuMs = new List<float>();
            var gpuMs = new List<float>();
            var frameLines = new List<string>();
            var workTotal = new Dictionary<string, double>();
            var workHits = new Dictionary<string, int>();
            var waitTotal = new Dictionary<string, double>();
            var waitHits = new Dictionary<string, int>();
            var skippedContainers = new HashSet<string>();
            double gcAllocMs = 0;
            int gcAllocHits = 0;
            int gpuFrames = 0;
            ResetRenderCounterCache();

            string callTreeDir = pending.detaildata_path + ".calls";
            Directory.CreateDirectory(callTreeDir);
            for (int fi = first; fi <= last; fi++)
            {
                var work = new MarkerBag();
                var wait = new MarkerBag();
                var gc = new GcBag();
                float gpu = 0f;
                FrameRenderStats render = EmptyRenderStats();
                float cpu = CollectFrameMarkers(
                    fi, work, wait, gc, skippedContainers,
                    ref gcAllocMs, ref gcAllocHits, out gpu, out render,
                    Path.Combine(callTreeDir, fi + ".json"));
                // 整段累计用每帧最外层 Time，不再把嵌套的同名 Time 加进去。
                MergeTotals(workTotal, workHits, work.time, work.timeHits);
                MergeTotals(waitTotal, waitHits, wait.time, wait.timeHits);
                cpuMs.Add(cpu);
                gpuMs.Add(gpu);
                if (gpu > 0.05f)
                    gpuFrames++;

                var line = new StringBuilder(512);
                line.Append("{\"frame\":").Append(fi);
                line.Append(",\"call_tree_available\":").Append(File.Exists(Path.Combine(callTreeDir, fi + ".json")) ? "true" : "false");
                line.Append(",\"cpu_ms\":").Append(F(cpu));
                line.Append(",\"gpu_ms\":").Append(F(gpu));
                // 等待用 Self 相加：父子都是等待时，Time 加总会把子节点再算一遍。
                line.Append(",\"wait_ms\":").Append(F(wait.SumSelf()));
                line.Append(",\"calls_total\":").Append(work.SumCalls());
                line.Append(",\"gc_alloc_bytes\":").Append(gc.total);
                line.Append(",\"cpu_top\":");
                AppendCompactMarkers(line, TopOf(work.time, PerFrameCpuLimit), work.timeHits);
                line.Append(",\"cpu_self_top\":");
                AppendCompactMarkers(line, TopOf(work.self, PerFrameCpuLimit), work.selfHits);
                line.Append(",\"calls_top\":");
                AppendCompactCalls(line, TopOfInt(work.calls, PerFrameCallsLimit), work.self);
                line.Append(",\"gc_top\":");
                AppendCompactGc(line, TopOfLong(gc.bytes, PerFrameGcLimit), gc.hits);
                line.Append(",\"wait_top\":");
                AppendCompactMarkers(line, TopOf(wait.time, PerFrameWaitLimit), wait.timeHits);
                AppendRenderStats(line, render);
                line.Append("}");
                frameLines.Add(line.ToString());
            }

            var topWork = TopOf(workTotal, 40);
            var topWait = TopOf(waitTotal, 20);
            double waitSum = 0;
            foreach (var kv in waitTotal)
                waitSum += kv.Value;

            double cpuSum = 0, cpuMax = 0, gpuSum = 0, gpuMax = 0;
            foreach (var v in cpuMs)
            {
                cpuSum += v;
                if (v > cpuMax) cpuMax = v;
            }
            foreach (var v in gpuMs)
            {
                gpuSum += v;
                if (v > gpuMax) gpuMax = v;
            }
            double avgCpu = cpuMs.Count > 0 ? cpuSum / cpuMs.Count : 0;
            double avgGpu = gpuMs.Count > 0 ? gpuSum / gpuMs.Count : 0;
            double p95 = Percentile(cpuMs, 0.95);
            double p99 = Percentile(cpuMs, 0.99);
            const float budget = 16.67f;
            int over = 0;
            foreach (var v in cpuMs)
                if (v > budget) over++;
            double hitchPct = cpuMs.Count > 0 ? 100.0 * over / cpuMs.Count : 0;

            var skippedList = new List<string>(skippedContainers);
            skippedList.Sort();

            var sb = new StringBuilder(256 * 1024);
            sb.Append("{\n");
            sb.Append("  \"schema\": \"ailearning.profiler.detaildata/v1\",\n");
            sb.Append("  \"raw_path\": \"").Append(Esc(pending.raw_path)).Append("\",\n");
            sb.Append("  \"data_path\": \"").Append(Esc(pending.data_path)).Append("\",\n");
            sb.Append("  \"detaildata_path\": \"").Append(Esc(pending.detaildata_path)).Append("\",\n");
            sb.Append("  \"unity_version\": \"").Append(Esc(pending.unity_version)).Append("\",\n");
            sb.Append("  \"editor_version\": \"").Append(Esc(pending.editor_version)).Append("\",\n");
            sb.Append("  \"generated_at\": \"").Append(Esc(DateTime.UtcNow.ToString("o"))).Append("\",\n");
            sb.Append("  \"overview\": {\n");
            sb.Append("    \"frame_count\": ").Append(cpuMs.Count).Append(",\n");
            sb.Append("    \"first_frame\": ").Append(first).Append(",\n");
            sb.Append("    \"last_frame\": ").Append(last).Append(",\n");
            sb.Append("    \"avg_cpu_ms\": ").Append(F(avgCpu)).Append(",\n");
            sb.Append("    \"avg_gpu_ms\": ").Append(F(avgGpu)).Append(",\n");
            sb.Append("    \"avg_frame_ms\": ").Append(F(avgCpu)).Append(",\n");
            sb.Append("    \"max_cpu_ms\": ").Append(F(cpuMax)).Append(",\n");
            sb.Append("    \"max_gpu_ms\": ").Append(F(gpuMax)).Append(",\n");
            sb.Append("    \"max_frame_ms\": ").Append(F(cpuMax)).Append(",\n");
            sb.Append("    \"p95_frame_ms\": ").Append(F(p95)).Append(",\n");
            sb.Append("    \"p99_frame_ms\": ").Append(F(p99)).Append(",\n");
            sb.Append("    \"budget_ms\": ").Append(F(budget)).Append(",\n");
            sb.Append("    \"over_budget_pct\": ").Append(F(hitchPct)).Append(",\n");
            sb.Append("    \"gpu_frames\": ").Append(gpuFrames).Append("\n");
            sb.Append("  },\n");
            sb.Append("  \"frames\": [\n");
            for (int i = 0; i < frameLines.Count; i++)
            {
                sb.Append("    ").Append(frameLines[i]);
                sb.Append(i + 1 < frameLines.Count ? ",\n" : "\n");
            }
            sb.Append("  ],\n");
            sb.Append("  \"cpu\": {\n");
            sb.Append("    \"scope\": \"session\",\n");
            sb.Append("    \"top_markers\": ");
            AppendMarkerArray(sb, "    ", topWork, workHits);
            sb.Append(",\n");
            sb.Append("    \"filtered_containers\": [");
            for (int i = 0; i < skippedList.Count; i++)
            {
                sb.Append("\"").Append(Esc(skippedList[i])).Append("\"");
                if (i + 1 < skippedList.Count) sb.Append(", ");
            }
            sb.Append("]\n");
            sb.Append("  },\n");
            sb.Append("  \"wait\": {\n");
            sb.Append("    \"scope\": \"session\",\n");
            sb.Append("    \"total_ms\": ").Append(F(waitSum)).Append(",\n");
            sb.Append("    \"top_markers\": ");
            AppendMarkerArray(sb, "    ", topWait, waitHits);
            sb.Append("\n");
            sb.Append("  },\n");
            sb.Append("  \"gc\": {\n");
            sb.Append("    \"sample_hits\": ").Append(gcAllocHits).Append(",\n");
            sb.Append("    \"sample_total_ms\": ").Append(F(gcAllocMs)).Append("\n");
            sb.Append("  },\n");
            sb.Append("  \"notes\": [\n");
            sb.Append("    \"全帧主线程 Top；详情用本文件，对话用从这里裁出的 .aidata\",\n");
            sb.Append("    \"avg_cpu_ms 是主线程 frameTimeMs；avg_gpu_ms 优先用 GPU 模块，没有则用 GPU Frame Time 计数器\",\n");
            sb.Append("    \"gpu_frames 为 0 表示这段 .raw 里两种 GPU 时间都没有\",\n");
            sb.Append("    \"热点帧的 batches/setpass/三角面来自 Rendering 计数器；Overdraw 和分辨率不在 .raw 里\",\n");
            sb.Append("    \"cpu_top 是 Time ms：含子调用，同名嵌套只留最外层；cpu_self_top 是 Self ms，不含子调用\",\n");
            sb.Append("    \"calls_top 按调用次数；gc_top 把 GC.Alloc 字节记到父函数上（metadata[0]）\"\n");
            sb.Append("  ]\n");
            sb.Append("}\n");
            return sb.ToString();
        }

        static double Percentile(List<float> values, double p)
        {
            if (values == null || values.Count == 0)
                return 0;
            var copy = new List<float>(values);
            copy.Sort();
            double idx = p * (copy.Count - 1);
            int lo = (int)Math.Floor(idx);
            int hi = (int)Math.Ceiling(idx);
            if (lo == hi)
                return copy[lo];
            double t = idx - lo;
            return copy[lo] * (1 - t) + copy[hi] * t;
        }

        static string F(double v)
        {
            if (double.IsNaN(v) || double.IsInfinity(v))
                return "0";
            return v.ToString("0.###", CultureInfo.InvariantCulture);
        }

        static string Esc(string s)
        {
            if (string.IsNullOrEmpty(s))
                return "";
            return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n").Replace("\r", "");
        }

        static void WriteResult(string path, bool ok, string error, Pending pending, int frames)
        {
            if (string.IsNullOrEmpty(path))
                return;
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(path) ?? ".");
                var sb = new StringBuilder();
                sb.Append("{\n");
                sb.Append("  \"ok\": ").Append(ok ? "true" : "false").Append(",\n");
                sb.Append("  \"error\": \"").Append(Esc(error ?? "")).Append("\",\n");
                sb.Append("  \"raw_path\": \"").Append(Esc(pending != null ? pending.raw_path : "")).Append("\",\n");
                sb.Append("  \"data_path\": \"").Append(Esc(pending != null ? pending.data_path : "")).Append("\",\n");
                sb.Append("  \"detaildata_path\": \"").Append(Esc(pending != null ? pending.detaildata_path : "")).Append("\",\n");
                sb.Append("  \"aidata_path\": \"").Append(Esc(pending != null ? pending.aidata_path : "")).Append("\",\n");
                sb.Append("  \"frame_count\": ").Append(frames).Append(",\n");
                sb.Append("  \"finished_at\": \"").Append(Esc(DateTime.UtcNow.ToString("o"))).Append("\"\n");
                sb.Append("}\n");
                File.WriteAllText(path, sb.ToString(), new UTF8Encoding(false));
            }
            catch (Exception e)
            {
                Debug.LogError("[AILearning] 写 result 失败: " + e.Message);
            }
        }

        static void FailAndMaybeQuit(Pending pending, string error)
        {
            Debug.LogError("[AILearning] " + error);
            if (pending != null && pending.mode == "convert")
            {
                string resultPath = pending.result_path;
                if (string.IsNullOrEmpty(resultPath) && !string.IsNullOrEmpty(pending.raw_path))
                    resultPath = Path.ChangeExtension(pending.raw_path, ".detaildata") + ".result.json";
                WriteResult(resultPath, false, error, pending, 0);
                _busy = false;
                _convertStarted = false;
                if (_quitAfterConvert)
                    EditorApplication.delayCall += () => EditorApplication.Exit(1);
                return;
            }
            if (pending == null || pending.mode != "convert")
                EditorUtility.DisplayDialog("AILearning Profiler", error, "OK");
            _busy = false;
        }
    }
}
