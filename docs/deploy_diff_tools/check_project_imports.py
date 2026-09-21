"""Scan project Python files for third-party imports and check them against the
deploy conda env's importable module set.

Usage:  python check_project_imports.py
Env:    QMCLAW_ENV_ZIP  (default D:\\semester4\\deploy\\qmclaw.zip)
        QMCLAW_ROOT     (default D:\\semester4\\QMClaw)
"""
import ast, os, re, sys, glob, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _envmeta import metadata_dir, env_zip

ROOT = os.environ.get("QMCLAW_ROOT", r"D:\semester4\QMClaw")
QZ = env_zip()
MD, _count = metadata_dir()

# ---- installed distribution names + importable top-level modules from the env ----
installed = {}
for f in glob.glob(os.path.join(MD, "*.METADATA")):
    txt = open(f, encoding="utf-8", errors="replace").read()
    n = re.search(r"^Name: (.+?)\s*$", txt, re.M)
    if n:
        installed[re.sub(r"[-_.]+", "-", n.group(1).strip()).lower()] = True
for extra in ("setuptools", "pip", "wheel"):
    installed.setdefault(extra, True)

top_level = set()
with zipfile.ZipFile(QZ) as z:
    for e in z.namelist():
        # declared top-level modules
        m = re.match(r"^qmclaw/Lib/site-packages/([^/]+)\.(dist-info|egg-info)/top_level\.txt$", e)
        if m:
            for line in z.read(e).decode("utf-8", "replace").splitlines():
                line = line.strip()
                if line:
                    top_level.add(line.split(".")[0])
        # anything importable that physically sits at the top of site-packages:
        #   <name>/__init__.py  -> package
        #   <name>.py           -> module
        #   <name>.pyd          -> extension module
        m2 = re.match(r"^qmclaw/Lib/site-packages/([^/]+)/(__init__\.py)$", e)
        if m2:
            top_level.add(m2.group(1))
        m3 = re.match(r"^qmclaw/Lib/site-packages/([^/]+)\.(py|pyd)$", e)
        if m3:
            top_level.add(m3.group(1))
    # packages added to sys.path by pywin32's .pth are not visible here; add them
    top_level.update({"win32", "win32con", "win32api", "win32gui", "win32ui",
                      "pythonwin", "pythoncom", "pywintypes"})

# ---- local project modules (must not be reported as external) ----
local = set()
for d in os.listdir(ROOT):
    p = os.path.join(ROOT, d)
    if os.path.isdir(p) and d not in (".git",):
        local.add(d)
        local.add(d.replace(" ", "_"))
for d in ("new_ctrl", "new_templates", "services", "backends", "scripts", "config"):
    local.add(d)

# ---- collect imports ----
imports = {}
py_files = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames
                   if d not in (".git", "node_modules", "__pycache__",
                                "deploy_diff_tools", "_metadata_cache")]
    for fn in filenames:
        if fn.endswith(".py"):
            py_files.append(os.path.join(dirpath, fn))

for fp in py_files:
    try:
        tree = ast.parse(open(fp, encoding="utf-8", errors="replace").read(), fp)
    except SyntaxError:
        continue
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                mods.add(node.module.split(".")[0])
    for m in mods:
        imports.setdefault(m, set()).add(os.path.relpath(fp, ROOT))

stdlib = set(sys.stdlib_module_names)
print(f"scanned {len(py_files)} python files, {len(imports)} distinct top-level imports\n")

missing, ok = [], []
for m in sorted(imports):
    if m in stdlib or m in local or m.startswith("_"):
        continue
    if m in top_level or m.lower() in installed or re.sub(r"[-_.]+", "-", m).lower() in installed:
        ok.append(m)
    else:
        missing.append(m)

print(f"=== resolvable in deploy env ({len(ok)}) ===")
for m in ok:
    print("  ", m)

print(f"\n=== NOT resolvable in deploy env ({len(missing)}) ===")
for m in missing:
    files = sorted(imports[m])
    print(f"  {m}")
    for f in files[:6]:
        print(f"        {f}")
    if len(files) > 6:
        print(f"        ... +{len(files)-6} more")
