// SwarmTrajectoryRecorder.cs  (with adjustable recording frequency)
// ... (header comments unchanged)

using System;
using System.Collections.Generic;
using System.IO;
using System.Text.RegularExpressions;
using UnityEngine;
using UnityEngine.SceneManagement;

#if UNITY_EDITOR
using UnityEditor;
#endif

public class SwarmTrajectoryRecorder : MonoBehaviour
{
    // -------------------- Configuration --------------------
    [Header("Swarm root discovery")]
    public string swarmRootTag = "Swarm";
    public string swarmRootName = "Swarm";
    public float rescanChildrenEverySec = 1f;

    [Header("Drone discovery")]
    public string droneComponentTypeName = "DroneController";

    [Header("Sampling")]
    [Tooltip("Samples per second (<=0 = every Update).")]
    public float sampleHz = 30f;
    public bool waitForDronesToStart = true;

    [Header("Recording frequency")]
    [Tooltip("Records per second (independent of sampleHz). 0 = record every sample.")]
    public float recordHz = 0f;
    [Tooltip("If recordHz==0, record every Nth sample (1 = every sample).")]
    public int recordEveryNthSample = 1;

    [Header("Lifecycle")]
    public bool dontDestroyOnLoad = true;
    public bool lazyDiscover = true;

    [Header("Output location")]
    public string outSubfolder = "Trajectories";
    public string pidOverride = "";

    [Header("Output naming")]
    public string sceneLabelOverride = "";
    public string setupSceneName = "Scene Selector";

    [Header("Quality of life")]
    public float autosaveEverySec = 0f;
    public bool enableHotkeySave = true;

    [Header("Main group selection")]
    public bool useNetworkForMainGroup = true;
    public bool includeAllUntilNetworkReady = true;

    [Header("Proximity fallback")]
    public float linkDistance = 3f;
    public int minMainGroupSize = 3;
    public bool useXZDistance = true;

    // -------------------- Internals --------------------
    public Transform swarmRoot;
    private readonly Dictionary<int, DroneTraj> _trajById = new Dictionary<int, DroneTraj>();
    private readonly List<Transform> _droneTransforms = new List<Transform>();
    private float _accum, _discoverTimer, _swarmFindTimer, _childrenRescanTimer, _autosaveTimer;
    private int _lastChildCount = -1;
    private bool _samplingEnabled;

    // NEW: recording schedulers
    private float _recordAccum = 0f;
    private int _sampleIndex = 0;

    // Singleton + save debounce
    private static SwarmTrajectoryRecorder _instance;
    private enum SaveReason { Auto, Manual, Final }
    private bool _finalized;
    private float _lastSaveRealtime;
    private const float SaveDebounceSec = 0.25f;

    // -------------------- Data types --------------------
    [Serializable]
    public struct TrajFrame
    {
        public float t;
        public float x, y, z;
        public byte g; // 0 = not in main group; 1 = in main group
    }

    [Serializable]
    public class DroneTraj
    {
        public int id;
        public string name;
        public List<TrajFrame> frames = new List<TrajFrame>(4096);
    }

    [Serializable]
    public class TrajectoryLog
    {
        public string scene;
        public string pid;
        public string haptics;
        public string order;
        public float sampleHz;
        public List<DroneTraj> trajectories = new List<DroneTraj>();
    }

    // -------------------- Unity lifecycle --------------------
    private void Awake()
    {
        if (_instance != null && _instance != this) { Destroy(gameObject); return; }
        _instance = this;

        if (dontDestroyOnLoad) DontDestroyOnLoad(gameObject);
        SceneManager.sceneLoaded += OnSceneLoaded;

        _accum = _autosaveTimer = 0f;
        _recordAccum = 0f; _sampleIndex = 0;
    }

    private void OnDestroy()
    {
        if (_instance == this) _instance = null;
        SceneManager.sceneLoaded -= OnSceneLoaded;
    }

    private void OnEnable()
    {
        TryFindSwarmRootNow();
        CollectDrones();
        _samplingEnabled = !waitForDronesToStart || _droneTransforms.Count > 0;
    }

    private void OnSceneLoaded(Scene s, LoadSceneMode m)
    {
        _trajById.Clear();
        _droneTransforms.Clear();
        _lastChildCount = -1;

        TryFindSwarmRootNow();
        CollectDrones();
        _samplingEnabled = !waitForDronesToStart || _droneTransforms.Count > 0;

        // reset schedulers on scene change
        _accum = _autosaveTimer = 0f;
        _recordAccum = 0f; _sampleIndex = 0;
    }

    private void Update()
    {
        if (enableHotkeySave && Input.GetKeyDown(KeyCode.F7)) TrySave(SaveReason.Manual);

        _swarmFindTimer += Time.deltaTime;
        if (!swarmRoot && _swarmFindTimer >= 0.5f)
        {
            _swarmFindTimer = 0f;
            TryFindSwarmRootNow();
            if (swarmRoot)
            {
                CollectDrones();
                _samplingEnabled = !waitForDronesToStart || _droneTransforms.Count > 0;
            }
        }

        if (lazyDiscover && swarmRoot && _droneTransforms.Count == 0)
        {
            _discoverTimer += Time.deltaTime;
            if (_discoverTimer > 0.5f)
            {
                _discoverTimer = 0f;
                int before = _droneTransforms.Count;
                CollectDrones();
                if (_droneTransforms.Count > before) _samplingEnabled = true;
            }
        }

        if (swarmRoot && rescanChildrenEverySec > 0f)
        {
            _childrenRescanTimer += Time.deltaTime;
            if (_childrenRescanTimer >= rescanChildrenEverySec)
            {
                _childrenRescanTimer = 0f;
                if (_lastChildCount != swarmRoot.childCount)
                {
                    _lastChildCount = swarmRoot.childCount;
                    CollectDrones();
                }
            }
        }

        if (!_samplingEnabled || _droneTransforms.Count == 0) return;

        if (sampleHz <= 0f)
        {
            // sample every Update; recording throttle via ShouldRecordThisSample(dt)
            bool recordNow = ShouldRecordThisSample(Time.deltaTime);
            SampleOnce(recordNow);
        }
        else
        {
            float period = 1f / sampleHz;
            _accum += Time.deltaTime;
            while (_accum >= period)
            {
                bool recordNow = ShouldRecordThisSample(period);
                SampleOnce(recordNow);
                _accum -= period;
            }
        }

        if (autosaveEverySec > 0f)
        {
            _autosaveTimer += Time.deltaTime;
            if (_autosaveTimer >= autosaveEverySec)
            {
                _autosaveTimer = 0f;
                TrySave(SaveReason.Auto);
            }
        }
    }

    private void OnApplicationQuit() => TrySave(SaveReason.Final);

    private void OnDisable()
    {
        var scene = SceneManager.GetActiveScene().name;
        if (scene == setupSceneName) return;
        TrySave(SaveReason.Final);
    }

    // -------------------- Recording schedule --------------------
    private bool ShouldRecordThisSample(float dt)
    {
        // Priority 1: rate-based throttle
        if (recordHz > 0f)
        {
            float recPeriod = 1f / recordHz;
            _recordAccum += dt;
            if (_recordAccum + 1e-6f >= recPeriod)
            {
                _recordAccum -= recPeriod;
                _sampleIndex++; // still increment sample index for consistency
                return true;
            }
            _sampleIndex++;
            return false;
        }

        // Priority 2: decimation-based throttle
        if (recordEveryNthSample <= 1)
        {
            _sampleIndex++;
            return true; // record every sample
        }

        _sampleIndex++;
        return (_sampleIndex % recordEveryNthSample) == 0;
    }

    // -------------------- Discovery --------------------
    private void TryFindSwarmRootNow()
    {
        if (swarmRoot) return;

        if (!string.IsNullOrEmpty(swarmRootTag))
        {
            var byTag = GameObject.FindWithTag(swarmRootTag);
            if (byTag) { swarmRoot = byTag.transform; _lastChildCount = swarmRoot.childCount; return; }
        }
        if (!string.IsNullOrEmpty(swarmRootName))
        {
            var byName = GameObject.Find(swarmRootName);
            if (byName) { swarmRoot = byName.transform; _lastChildCount = swarmRoot.childCount; }
        }
    }

    private void CollectDrones()
    {
        _droneTransforms.Clear();
        if (!swarmRoot) return;

        var type = GetTypeByName(droneComponentTypeName);
        if (type != null)
        {
            var comps = swarmRoot.GetComponentsInChildren(type, true);
            foreach (var c in comps)
            {
                var tr = ((Component)c).transform;
                if (!_droneTransforms.Contains(tr)) _droneTransforms.Add(tr);
                EnsureTrajFor(tr);
            }
        }
        else
        {
            foreach (Transform tr in swarmRoot.GetComponentsInChildren<Transform>(true))
            {
                if (tr == swarmRoot) continue;
                if (!_droneTransforms.Contains(tr)) _droneTransforms.Add(tr);
                EnsureTrajFor(tr);
            }
        }
    }

    private void EnsureTrajFor(Transform tr)
    {
        int id = GetStableId(tr);
        if (!_trajById.ContainsKey(id))
            _trajById[id] = new DroneTraj { id = id, name = tr.name };
    }

    private static Type GetTypeByName(string typeName)
    {
        if (string.IsNullOrEmpty(typeName)) return null;
        foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
        {
            var t = asm.GetType(typeName);
            if (t != null) return t;
        }
        return null;
    }

    private int GetStableId(Transform t)
    {
        var compType = GetTypeByName(droneComponentTypeName);
        if (compType != null)
        {
            var comp = t.GetComponent(compType);
            if (comp != null)
            {
                var f = compType.GetField("Id") ?? compType.GetField("DroneId");
                if (f != null && f.FieldType == typeof(int)) return (int)f.GetValue(comp);
                var p = compType.GetProperty("Id") ?? compType.GetProperty("DroneId");
                if (p != null && p.PropertyType == typeof(int)) return (int)p.GetValue(comp);
            }
        }
        return t.GetInstanceID();
    }

    // -------------------- Sampling --------------------
    private void SampleOnce(bool writeThisSample)
    {
        int n = _droneTransforms.Count;
        if (n == 0) return;

        float t = Time.time;

        // If not writing, we can early-exit to avoid extra work.
        if (!writeThisSample) return;

        var positions = new Vector3[n];
        var ids       = new int[n];
        for (int i = 0; i < n; i++)
        {
            var tr = _droneTransforms[i];
            if (!tr) continue;
            positions[i] = tr.position;
            ids[i] = GetStableId(tr);
            if (!_trajById.TryGetValue(ids[i], out _))
                _trajById[ids[i]] = new DroneTraj { id = ids[i], name = tr.name };
        }

        // Compute main group flags (for frames we actually write)
        var inMain = new bool[n];
        ComputeMainGroupFlags(positions, inMain);

        // Record frames
        for (int i = 0; i < n; i++)
        {
            if (!_trajById.TryGetValue(ids[i], out var traj)) continue;
            Vector3 p = positions[i];
            traj.frames.Add(new TrajFrame
            {
                t = t,
                x = p.x, y = p.y, z = p.z,
                g = (byte)(inMain[i] ? 1 : 0)
            });
        }
    }

    // -------------------- Main-group logic --------------------
    private void ComputeMainGroupFlags(Vector3[] positions, bool[] inMain)
    {
        Array.Clear(inMain, 0, inMain.Length);
        int n = positions.Length;
        if (n == 0) return;

        if (useNetworkForMainGroup)
        {
            var net = swarmModel.network;
            if (net != null && net.largestComponent != null && net.largestComponent.Count > 0)
            {
                var mainSet = new HashSet<DroneFake>(net.largestComponent);
                for (int i = 0; i < n; i++)
                {
                    var tr = _droneTransforms[i];
                    if (!tr) continue;
                    var dc = tr.GetComponent<DroneController>();
                    var df = (dc != null) ? dc.droneFake : null;
                    bool isInMain =
                        (df != null && mainSet.Contains(df)) ||
                        (df != null && net.IsInMainNetwork(df));
                    inMain[i] = isInMain;
                }

                if (minMainGroupSize > 1)
                {
                    int count = 0; for (int i = 0; i < n; i++) if (inMain[i]) count++;
                    if (count < minMainGroupSize) Array.Clear(inMain, 0, n);
                }
                return;
            }

            if (includeAllUntilNetworkReady)
            {
                for (int i = 0; i < n; i++) inMain[i] = true;
                return;
            }
        }

        ProximityFallback(positions, inMain);
    }

    private void ProximityFallback(Vector3[] positions, bool[] inMain)
    {
        int n = positions.Length;
        if (n == 0) return;

        int[] parent = new int[n];
        int[] size = new int[n];
        for (int i = 0; i < n; i++) { parent[i] = i; size[i] = 1; }

        int Find(int a) { while (parent[a] != a) { parent[a] = parent[parent[a]]; a = parent[a]; } return a; }
        void Union(int a, int b)
        {
            a = Find(a); b = Find(b);
            if (a == b) return;
            if (size[a] < size[b]) { var t = a; a = b; b = t; }
            parent[b] = a; size[a] += size[b];
        }

        float r2 = linkDistance * linkDistance;
        for (int i = 0; i < n; i++)
        for (int j = i + 1; j < n; j++)
        {
            float dx = positions[i].x - positions[j].x;
            float dz = positions[i].z - positions[j].z;
            float d2 = useXZDistance
                ? (dx * dx + dz * dz)
                : (dx * dx + dz * dz + (positions[i].y - positions[j].y) * (positions[i].y - positions[j].y));
            if (d2 <= r2) Union(i, j);
        }

        var counts = new Dictionary<int, int>();
        int bestRoot = -1, best = 0;
        for (int i = 0; i < n; i++)
        {
            int r = Find(i);
            counts[r] = counts.TryGetValue(r, out var cur) ? (cur + 1) : 1;
            if (counts[r] > best) { best = counts[r]; bestRoot = r; }
        }

        if (best < Mathf.Max(1, minMainGroupSize))
        {
            Array.Clear(inMain, 0, n);
            return;
        }

        for (int i = 0; i < n; i++)
            inMain[i] = (Find(i) == bestRoot);
    }

    // -------------------- Saving --------------------
    private void TrySave(SaveReason reason)
    {
        if (_finalized && reason != SaveReason.Auto) return;
        if (Time.realtimeSinceStartup - _lastSaveRealtime < SaveDebounceSec) return;
        _lastSaveRealtime = Time.realtimeSinceStartup;

        Save();

        if (reason == SaveReason.Final) _finalized = true;
#if UNITY_EDITOR
        Debug.Log($"[SwarmTrajectoryRecorder] Save() wrote file. finalized={_finalized} time={Time.time:F2}");
#endif
    }

    public void Save()
    {
        bool any = false;
        foreach (var kv in _trajById) { if (kv.Value.frames.Count > 0) { any = true; break; } }
        if (!any && _droneTransforms.Count > 0) SampleOnce(true);

        var log = new TrajectoryLog
        {
            scene = SceneManager.GetActiveScene().name,
            pid = ResolvePid(),
            haptics = ResolveHaptics(),
            order = ResolveOrder(),
            sampleHz = sampleHz <= 0 ? -1f : sampleHz,
            trajectories = new List<DroneTraj>(_trajById.Values)
        };

        string root;
#if UNITY_EDITOR
        root = Path.Combine(Application.dataPath, "Data", log.pid, outSubfolder);
#else
        root = Path.Combine(Application.persistentDataPath, "Data", log.pid, outSubfolder);
#endif
        Directory.CreateDirectory(root);

        string activeScene = SceneManager.GetActiveScene().name;
        string label = !string.IsNullOrEmpty(sceneLabelOverride) ? sceneLabelOverride :
                       (ResolveSelectedSceneLabel() ?? ((activeScene == setupSceneName) ? "UnknownScene" : activeScene));
        string safeScene = MakeFileSafe(label);

        string stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
        string fileName = $"{safeScene}_{log.haptics}_{log.order}_{stamp}_traj.json";
        string full = Path.Combine(root, fileName);

        File.WriteAllText(full, JsonUtility.ToJson(log, true));
        Debug.Log($"[SwarmTrajectoryRecorder] Saved {log.trajectories.Count} drones to:\n{full}");

#if UNITY_EDITOR
        if (full.StartsWith(Application.dataPath)) AssetDatabase.Refresh();
#endif
    }

    private static string MakeFileSafe(string s)
    {
        if (string.IsNullOrEmpty(s)) return "Scene";
        s = Regex.Replace(s, @"\s+", "_");
        foreach (char c in Path.GetInvalidFileNameChars()) s = s.Replace(c.ToString(), "");
        return s;
    }

    private string ResolvePid()
    {
        if (!string.IsNullOrEmpty(pidOverride)) return pidOverride;
        var t = GetTypeByName("SceneSelectorScript");
        if (t != null)
        {
            var f = t.GetField("pid");
            if (f != null && f.FieldType == typeof(string))
            {
                var v = (string)f.GetValue(null);
                if (!string.IsNullOrEmpty(v)) return v;
            }
        }
        return "PID_Default";
    }

    private string ResolveHaptics()
    {
        var t = GetTypeByName("SceneSelectorScript");
        if (t != null)
        {
            var f = t.GetField("_haptics");
            if (f != null && f.FieldType == typeof(bool))
                return ((bool)f.GetValue(null)) ? "H" : "NH";
        }
        return "NH";
    }

    private string ResolveOrder()
    {
        var t = GetTypeByName("SceneSelectorScript");
        if (t != null)
        {
            var f = t.GetField("_order");
            if (f != null && f.FieldType == typeof(bool))
                return ((bool)f.GetValue(null)) ? "O" : "NO";
        }
        return "NO";
    }

    private string ResolveSelectedSceneLabel()
    {
        var t = GetTypeByName("SceneSelectorScript");
        if (t == null) return null;
        string[] fieldNames = { "selectedSceneName", "sceneToLoad", "targetScene", "detailScene", "SelectedLevel", "SelectedScene" };
        foreach (var fn in fieldNames)
        {
            var f = t.GetField(fn);
            if (f != null && f.FieldType == typeof(string))
            {
                var v = f.GetValue(null) as string;
                if (!string.IsNullOrEmpty(v)) return v;
            }
        }
        string[] propNames = { "SelectedSceneName", "SceneToLoad", "TargetScene", "DetailScene", "SelectedLevel", "SelectedScene" };
        foreach (var pn in propNames)
        {
            var p = t.GetProperty(pn);
            if (p != null && p.PropertyType == typeof(string))
            {
                var v = p.GetValue(null) as string;
                if (!string.IsNullOrEmpty(v)) return v;
            }
        }
        return null;
    }
}
