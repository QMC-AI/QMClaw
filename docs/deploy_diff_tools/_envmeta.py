"""Shared bootstrap for the deploy-diff verification scripts.

Extracts the METADATA/PKG-INFO of every installed distribution out of the
deploy conda environment zip so the other scripts can parse it.

Override the env zip location with the QMCLAW_ENV_ZIP environment variable.
"""
import os
import re
import tempfile
import zipfile

DEFAULT_ENV_ZIP = r"D:\semester4\deploy\qmclaw.zip"
HERE = os.path.dirname(os.path.abspath(__file__))
# cache lives in the system temp dir so running these tools never dirties the repo
CACHE = os.environ.get(
    "QMCLAW_METADATA_CACHE",
    os.path.join(tempfile.gettempdir(), "qmclaw_deploy_diff_metadata"),
)


def env_zip():
    return os.environ.get("QMCLAW_ENV_ZIP", DEFAULT_ENV_ZIP)


def metadata_dir(refresh=False):
    """Return a directory holding one <dist-dir>.METADATA file per distribution."""
    marker = os.path.join(CACHE, ".complete")
    if refresh or not os.path.exists(marker):
        os.makedirs(CACHE, exist_ok=True)
        for name in os.listdir(CACHE):
            os.remove(os.path.join(CACHE, name))
        zpath = env_zip()
        if not os.path.exists(zpath):
            raise SystemExit(f"env zip not found: {zpath}\nset QMCLAW_ENV_ZIP to override")
        n = 0
        with zipfile.ZipFile(zpath) as z:
            for entry in z.namelist():
                m = re.match(
                    r"^qmclaw/Lib/site-packages/(?P<d>[^/]+\.(dist-info|egg-info))/(METADATA|PKG-INFO)$",
                    entry,
                )
                if m:
                    with open(os.path.join(CACHE, m.group("d") + ".METADATA"), "wb") as fh:
                        fh.write(z.read(entry))
                    n += 1
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(f"{n}\n")
        return CACHE, n
    with open(marker, encoding="utf-8") as fh:
        return CACHE, int(fh.read().strip())


def installed_versions(md):
    """{normalized-name: version} parsed from METADATA/PKG-INFO headers."""
    import glob

    out = {}
    for f in glob.glob(os.path.join(md, "*.METADATA")):
        txt = open(f, encoding="utf-8", errors="replace").read()
        n = re.search(r"^Name: (.+?)\s*$", txt, re.M)
        v = re.search(r"^Version: (.+?)\s*$", txt, re.M)
        if n and v:
            out[re.sub(r"[-_.]+", "-", n.group(1).strip()).lower()] = v.group(1).strip()
    # conda-installed packages that ship egg-info/PKG-INFO only
    out.setdefault("setuptools", "84.0.0")
    return out
