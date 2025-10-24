// SwarmTrajectoriesOnly.cs
// Record only trajectories of all drones into a single JSON file.
// Editor: saves under Assets/Data/<PID>/<outSubfolder>
// Build : saves under persistentDataPath/Data/<PID>/<outSubfolder>

using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.SceneManagement;

#if UNITY_EDITOR
using UnityEditor; // AssetDatabase.Refresh, EditorUtility.RevealInFinder
#endif

public class SwarmTrajectoriesOnly : MonoBehaviour
{
    [Header("Runtime Swarm discovery")]
    [Tooltip("Tag of the Swarm root (optional but recommended).")]
    public string swarmRootTag = "Swarm";
    [Tooltip("Fallback: exact name of the Swarm root if tag lookup fails.")]
    public string swarmRootName = "Swarm";
    [Tooltip("Re-scan Swarm children periodically to catch late-spawned drones (0=off).")]
    public float rescanChildrenEverySec = 1f;

    [Header("Drone discovery")]
    [Tooltip("Component type name on each drone (e.g., DroneController).")]
    public string droneComponentTypeName = "DroneController";

    [Header("Sampling")]
    [Tooltip("Samples per second. Set 0 to sample every frame.")]
    public float sampleHz = 30f;
    [Tooltip("Start sampling only after at least one drone is found.")]
    public bool waitForDronesToStart = true;

    [Header("Lifecycle")]
    [Tooltip("Keep this recorder alive across scene loads.")]
    public bool dontDestroyOnLoad = true;
    [Tooltip("Keep trying to discover swarm/drones while running.")]
    public bool lazyDiscover = true;

    [Header("Output")]
    [Tooltip("Subfolder under Assets/Data/<PID>/ or persistentDataPath/Data/<PID>/.")]
    public string outSubfolder = "Trajectories";
    [Tooltip("If empty, tries SceneSelectorScript.pid; else uses PID_Default.")]
    public string pidOverride = "";

    [Header("Quality of life")]
    [Tooltip("Autosave every N seconds (0 = off).")]
    public float autosaveEverySec = 0f;
    [Tooltip("Name of your setup/selector scene (skip saving when disabling there).")]
    public string setupSceneName = "Scene Selector";
    [Tooltip("Press F7 to force a save while playing.")]
    public bool enableHotkeySave = true;

    // ---- Internals ----
    public Transform swarmRoot; // assigned once found
    private readonly Dictionary<int, DroneTraj> _trajById = new Dictionary<int, DroneTraj>();
    private readonly List<Transform> _droneTransforms = new List<Transform>();
    private float _accum, _discoverTimer, _swarmFindTimer, _childrenRescanTimer, _autosaveTimer;
    private int _lastChildCount = -1;
    private bool _samplingEnabled;

    // ---- Serializable data types ----
    [Serializable] public struct TrajFrame { public float t, x, y, z; }
    [Serializable] public class DroneTraj { public int id; public string name; public List<TrajFrame> frames = new List<TrajFrame>(4096); }
    [Serializable] public class TrajectoryLog
    {
        public string scene;
        public string pid;
        public string haptics;  // "H" or "NH"
        public string order;    // "O" or "NO"
        public float sampleHz;
        public List<DroneTraj> trajectories = new List<DroneTraj>();
    }

    // ---------------- Unity ----------------
    void Awake()
    {
        if (dontDestroyOnLoad)
        {
            DontDestroyOnLoad(gameObject);
            Debug.Log("[SwarmTrajectoriesOnly] DontDestroyOnLoad enabled.");
        }
        SceneManager.sceneLoaded += OnSceneLoaded;
    }

    void OnDestroy() => SceneManager.sceneLoaded -= OnSceneLoaded;

    void OnEnable()
    {
        Debug.Log($"[SwarmTrajectoriesOnly] OnEnable. Editor? {Application.isEditor}. persistentDataPath={Application.persistentDataPath}");
        _accum = _autosaveTimer = 0f;
        TryFindSwarmRootNow();
        CollectDrones(); // no-op until swarm found
        _samplingEnabled = !waitForDronesToStart || _droneTransforms.Count > 0;
    }

    void OnSceneLoaded(Scene s, LoadSceneMode m)
    {
        Debug.Log($"[SwarmTrajectoriesOnly] Scene loaded: {s.name}");
        _trajById.Clear();
        _droneTransforms.Clear();
        _lastChildCount = -1;
        TryFindSwarmRootNow();
        CollectDrones();
        _samplingEnabled = !waitForDronesToStart || _droneTransforms.Count > 0;
    }

    void Update()
    {
        if (enableHotkeySave && Input.GetKeyDown(KeyCode.F7))
        {
            Debug.Log("[SwarmTrajectoriesOnly] F7 -> Save()");
            TrySave();
        }

        // Find Swarm root if it appears later
        _swarmFindTimer += Time.deltaTime;
        if (!swarmRoot && _swarmFindTimer >= 0.5f)
        {
            _swarmFindTimer = 0f;
            TryFindSwarmRootNow();
            if (swarmRoot) { CollectDrones(); _samplingEnabled = !waitForDronesToStart || _droneTransforms.Count > 0; }
        }

        // Lazy discover drones if none yet
        if (lazyDiscover && swarmRoot && _droneTransforms.Count == 0)
        {
            _discoverTimer += Time.deltaTime;
            if (_discoverTimer > 0.5f)
            {
                _discoverTimer = 0f;
                int before = _droneTransforms.Count;
                CollectDrones();
                if (_droneTransforms.Count != before)
                    Debug.Log($"[SwarmTrajectoriesOnly] Lazy discovered {_droneTransforms.Count} drones.");
                if (_droneTransforms.Count > 0) _samplingEnabled = true;
            }
        }

        // Re-scan children periodically (handles drones spawned after start)
        if (swarmRoot && rescanChildrenEverySec > 0f)
        {
            _childrenRescanTimer += Time.deltaTime;
            if (_childrenRescanTimer >= rescanChildrenEverySec)
            {
                _childrenRescanTimer = 0f;
                if (_lastChildCount != swarmRoot.childCount)
                {
                    _lastChildCount = swarmRoot.childCount;
                    int before = _droneTransforms.Count;
                    CollectDrones();
                    if (_droneTransforms.Count != before)
                        Debug.Log($"[SwarmTrajectoriesOnly] Swarm child change → drones: {before} → {_droneTransforms.Count}");
                }
            }
        }

        if (!_samplingEnabled || _droneTransforms.Count == 0) return;

        // Fixed-rate sampling
        if (sampleHz <= 0f) SampleOnce();
        else
        {
            _accum += Time.deltaTime;
            float period = 1f / sampleHz;
            while (_accum >= period) { SampleOnce(); _accum -= period; }
        }

        // Autosave (optional)
        if (autosaveEverySec > 0f)
        {
            _autosaveTimer += Time.deltaTime;
            if (_autosaveTimer >= autosaveEverySec)
            {
                _autosaveTimer = 0f;
                TrySave();
            }
        }
    }

    void OnApplicationQuit()
    {
        Debug.Log("[SwarmTrajectoriesOnly] OnApplicationQuit -> Save()");
        TrySave();
    }

    void OnDisable()
    {
        // Avoid saving during early setup/selector scene switch
        var scene = SceneManager.GetActiveScene().name;
        if (scene == setupSceneName) return;
        Debug.Log("[SwarmTrajectoriesOnly] OnDisable -> Save()");
        TrySave();
    }

    // ---------------- Core ----------------
    private void TryFindSwarmRootNow()
    {
        if (swarmRoot) return;

        if (!string.IsNullOrEmpty(swarmRootTag))
        {
            var byTag = GameObject.FindWithTag(swarmRootTag);
            if (byTag)
            {
                swarmRoot = byTag.transform;
                _lastChildCount = swarmRoot.childCount;
                Debug.Log("[SwarmTrajectoriesOnly] Found Swarm by tag: " + swarmRootTag);
                return;
            }
        }
        if (!string.IsNullOrEmpty(swarmRootName))
        {
            var byName = GameObject.Find(swarmRootName);
            if (byName)
            {
                swarmRoot = byName.transform;
                _lastChildCount = swarmRoot.childCount;
                Debug.Log("[SwarmTrajectoriesOnly] Found Swarm by name: " + swarmRootName);
            }
        }
    }

    private void CollectDrones()
    {
        _droneTransforms.Clear();
        if (!swarmRoot) { Debug.Log("[SwarmTrajectoriesOnly] CollectDrones skipped (no swarmRoot yet)."); return; }

        var type = GetTypeByName(droneComponentTypeName);
        if (type != null)
        {
            var comps = swarmRoot.GetComponentsInChildren(type, true); // include inactive
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

        Debug.Log($"[SwarmTrajectoriesOnly] CollectDrones -> {_droneTransforms.Count} transforms.");
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
        return t.GetInstanceID(); // fallback
    }

    private void SampleOnce()
    {
        float t = Time.time;
        for (int i = 0; i < _droneTransforms.Count; i++)
        {
            var tr = _droneTransforms[i];
            if (!tr) continue;

            int id = GetStableId(tr);
            var traj = _trajById[id];
            Vector3 p = tr.position;
            traj.frames.Add(new TrajFrame { t = t, x = p.x, y = p.y, z = p.z });
        }
    }

    private void TrySave()
    {
        try { Save(); }
        catch (Exception ex) { Debug.LogError("[SwarmTrajectoriesOnly] Save failed: " + ex); }
    }

    public void Save()
    {
        // Ensure at least one sample so the file isn’t empty
        bool any = false;
        foreach (var kv in _trajById) { if (kv.Value.frames.Count > 0) { any = true; break; } }
        if (!any && _droneTransforms.Count > 0) SampleOnce();

        var log = new TrajectoryLog
        {
            scene = SceneManager.GetActiveScene().name,
            pid = ResolvePid(),
            haptics = ResolveHaptics(),
            order = ResolveOrder(),
            sampleHz = sampleHz <= 0 ? -1f : sampleHz
        };
        foreach (var kv in _trajById) log.trajectories.Add(kv.Value);

        // -------- Choose output root --------
        string root;
#if UNITY_EDITOR
        root = Path.Combine(Application.dataPath, "Data", log.pid, outSubfolder); // Editor: inside Assets/
#else
        root = Path.Combine(Application.persistentDataPath, "Data", log.pid, outSubfolder); // Build: writable
#endif
        Directory.CreateDirectory(root);

        string stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
        string fileName = $"{MakeSafe(log.scene)}_{log.haptics}_{log.order}_{stamp}_traj.json";
        string full = Path.Combine(root, fileName);

        File.WriteAllText(full, JsonUtility.ToJson(log, false));
        Debug.Log($"[SwarmTrajectoriesOnly] Saved {log.trajectories.Count} drones to:\n{full}");

#if UNITY_EDITOR
        // Make it appear in Project view if we wrote under Assets/
        if (full.StartsWith(Application.dataPath)) AssetDatabase.Refresh();
        // Optional: reveal the file automatically
        // EditorUtility.RevealInFinder(full);
#endif
    }

    private static string MakeSafe(string s)
    {
        foreach (char c in Path.GetInvalidFileNameChars()) s = s.Replace(c, '_');
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
}
