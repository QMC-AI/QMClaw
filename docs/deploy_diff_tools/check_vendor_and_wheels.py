"""Check vendor.zip project declarations against the deploy env's installed set.

Usage:  python check_vendor_and_wheels.py
Env:    QMCLAW_ENV_ZIP    (default D:\\semester4\\deploy\\qmclaw.zip)
        QMCLAW_VENDOR_ZIP (default <DEPLOY>\\vendor.zip)
        QMCLAW_DEPLOY_DIR (default D:\\semester4\\deploy) - where the *.whl live
"""
import re, glob, os, zipfile, sys, tomllib
from packaging.requirements import Requirement
from packaging.version import Version, InvalidVersion

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _envmeta import metadata_dir

DEPLOY = os.environ.get("QMCLAW_DEPLOY_DIR", r"D:\semester4\deploy")
VENDOR = os.environ.get("QMCLAW_VENDOR_ZIP", os.path.join(DEPLOY, "vendor.zip"))
ENV_MARKER = {"python_version": "3.11", "python_full_version": "3.11.16",
              "sys_platform": "win32", "platform_system": "Windows", "os_name": "nt",
              "platform_machine": "AMD64", "implementation_name": "cpython",
              "platform_python_implementation": "CPython", "extra": ""}

MD, _count = metadata_dir()

installed = {}
for f in glob.glob(os.path.join(MD, "*.METADATA")):
    txt = open(f, encoding="utf-8", errors="replace").read()
    n = re.search(r"^Name: (.+?)\s*$", txt, re.M)
    v = re.search(r"^Version: (.+?)\s*$", txt, re.M)
    if n and v:
        installed[re.sub(r"[-_.]+", "-", n.group(1).strip()).lower()] = v.group(1).strip()
# conda-side additions that ship no METADATA
installed.setdefault("setuptools", "84.0.0")
installed.setdefault("python-abi", "3.11")

z = zipfile.ZipFile(VENDOR)
MEMBERS = ["hermes-agent/pyproject.toml", "QubitClient/pyproject.toml",
           "Quantum-Calibration-Agent-Blueprint/pyproject.toml"]

for m in MEMBERS:
    data = tomllib.loads(z.read(m).decode("utf-8"))
    proj = data.get("project", {})
    name = proj.get("name", m)
    ver = proj.get("version", "(dynamic)")
    deps = proj.get("dependencies", [])
    print(f"\n===== {m}  ->  {name} {ver}  ({len(deps)} mandatory deps) =====")
    bad = 0
    for raw in deps:
        try:
            r = Requirement(raw)
        except Exception:
            print(f"   ?  unparsed: {raw}")
            continue
        if r.marker is not None:
            try:
                if not r.marker.evaluate(ENV_MARKER):
                    continue
            except Exception:
                pass
        k = re.sub(r"[-_.]+", "-", r.name).lower()
        if k not in installed:
            print(f"   MISSING   {raw}")
            bad += 1
            continue
        have = installed[k]
        try:
            ok = r.specifier.contains(Version(have), prereleases=True)
        except InvalidVersion:
            ok = True
        if not ok:
            print(f"   CONFLICT  {raw}   -> present {have}")
            bad += 1
    print(f"   -> {bad} problem(s) out of {len(deps)}")

# deploy wheels' mandatory deps that ARE installed
print("\n\n===== deploy wheels -> installed env =====")
import zipfile as zf
for whl in sorted(glob.glob(os.path.join(DEPLOY, "*.whl"))):
    wz = zf.ZipFile(whl)
    md = [n for n in wz.namelist() if n.endswith(".dist-info/METADATA")][0]
    txt = wz.read(md).decode("utf-8", "replace")
    nm = re.search(r"^Name: (.+?)\s*$", txt, re.M).group(1).strip()
    vr = re.search(r"^Version: (.+?)\s*$", txt, re.M).group(1).strip()
    reqs = [x.strip() for x in re.findall(r"^Requires-Dist: (.+?)\s*$", txt, re.M)]
    print(f"\n--- {os.path.basename(whl)}  ({nm} {vr}) ---")
    k = re.sub(r"[-_.]+", "-", nm).lower()
    print(f"   installed in env: {installed.get(k, 'NO')}")
    for raw in reqs:
        if "extra ==" in raw:
            continue
        try:
            r = Requirement(raw)
        except Exception:
            print(f"   dep UNPARSED  {raw}")
            continue
        kk = re.sub(r"[-_.]+", "-", r.name).lower()
        if kk not in installed:
            print(f"   dep MISSING   {raw}")
            continue
        have = installed[kk]
        try:
            ok = r.specifier.contains(Version(have), prereleases=True)
        except InvalidVersion:
            ok = True
        if not ok:
            print(f"   dep CONFLICT  {raw}  -> present {have}")
