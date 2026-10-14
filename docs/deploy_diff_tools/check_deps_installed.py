"""Check MANDATORY (non-extra) dependency constraints of the deploy conda env
against the versions actually present in that env.

Usage:  python check_deps_installed.py
Env:    QMCLAW_ENV_ZIP  (default D:\\semester4\\deploy\\qmclaw.zip)
"""
import re, glob, os, sys
from email.parser import Parser
from packaging.requirements import Requirement
from packaging.version import Version, InvalidVersion

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _envmeta import metadata_dir

MD, _count = metadata_dir()

installed, metas = {}, {}
for f in glob.glob(os.path.join(MD, "*.METADATA")):
    with open(f, "r", encoding="utf-8", errors="replace") as fh:
        msg = Parser().parsestr(fh.read())
    name, ver = msg.get("Name"), msg.get("Version")
    if not name or not ver:
        continue
    key = re.sub(r"[-_.]+", "-", name).lower()
    installed[key] = ver
    metas[key] = (name, ver, msg.get_all("Requires-Dist") or [])
installed.setdefault("setuptools", "84.0.0")

ENV = {"python_version": "3.11", "python_full_version": "3.11.16",
       "sys_platform": "win32", "platform_system": "Windows", "os_name": "nt",
       "platform_machine": "AMD64", "implementation_name": "cpython",
       "platform_python_implementation": "CPython", "extra": ""}

missing, conflicts, skipped = [], [], 0
for key, (name, ver, reqs) in sorted(metas.items()):
    for raw in reqs:
        try:
            r = Requirement(raw)
        except Exception:
            continue
        if r.marker is not None:
            if "extra" in str(r.marker):
                skipped += 1
                continue
            try:
                if not r.marker.evaluate(ENV):
                    continue
            except Exception:
                pass
        dk = re.sub(r"[-_.]+", "-", r.name).lower()
        if dk not in installed:
            missing.append((name, ver, raw))
            continue
        have = installed[dk]
        try:
            ok = r.specifier.contains(Version(have), prereleases=True)
        except InvalidVersion:
            ok = True
        if not ok:
            conflicts.append((name, ver, raw, have))

print(f"distributions: {len(installed)}   extra-gated reqs skipped: {skipped}")
print(f"\n=== {len(conflicts)} VERSION CONFLICTS (mandatory deps) ===")
for name, ver, raw, have in conflicts:
    print(f"  {name}=={ver}  requires  {raw}   ->  present {have}")
print(f"\n=== {len(missing)} MISSING mandatory deps ===")
for name, ver, raw in missing:
    print(f"  {name}=={ver}  requires  {raw}")
