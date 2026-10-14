#!/usr/bin/env python
"""Idempotently wire QMClaw's repository skills into the Hermes agent runtime.

Why this script exists
----------------------
Two different readers look for skills, and they read *different* directories:

* the Web/HTTP side (``services/hermes_service/skills.py``) reads
  ``<server>/data/skills`` — which historically was a **hand-made directory
  junction** to ``<repo>/skills``.  ``data/`` is in ``.gitignore``, so a fresh
  clone on another machine has no junction at all and the page comes back empty.
* the Hermes agent side (``skills_list`` in ``tools/skills_tool.py`` and the
  system-prompt skill index in ``agent/prompt_builder.py``) reads
  ``$HERMES_HOME/skills``.  With ``HERMES_HOME=<server>/data/hermes`` that is
  ``data/hermes/skills`` — an empty directory — so the agent silently lists no
  skills at all even though the repo ships seven.

Hermes already has a first-class mechanism for pointing at skills that live
outside ``HERMES_HOME``: ``skills.external_dirs`` in ``config.yaml``, consumed by
``agent/skill_utils.py::get_external_skills_dirs()`` (supports ``~``/``${VAR}``
expansion, keeps only directories that exist, skips the local skills dir, and
caches by config mtime+size).  Skills reached that way are **read-only** to the
agent (``tools/skill_manager_guards.py``), so the tracked repository stays clean
while the agent can read and run them.

So this script does the two things that used to be done by hand and therefore
broke on every new machine:

1. add ``<repo>/skills`` to ``skills.external_dirs`` in ``$HERMES_HOME/config.yaml``
   (merged, never replacing other keys; no write when nothing changes), and
2. make ``<server>/data/skills`` a junction to ``<repo>/skills``.

Both steps are idempotent and safe to run at every service start.  Fixing this
here — instead of patching ``vendor/hermes-agent/**`` or
``services/hermes_service/**`` — keeps the upstream vendored tree byte-identical
and uses only mechanisms upstream already supports.

Exit status
-----------
0  : everything is wired (including "junction could not be created" — the caller
     is a start script and must not refuse to boot over a cosmetic link), or the
     link step warned and skipped a non-empty real directory.
!= 0: a *config* failure (missing repo skills dir, unreadable/invalid YAML, a
     structurally unusable config, or a failed atomic write).  Nothing is
     overwritten in those cases.

Deleting or rewriting ``config.yaml`` drops YAML comments: the file is loaded with
``yaml.safe_load`` and written back with ``safe_dump``.  The file is only written
when a change is actually needed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    print("[fatal] PyYAML is required (pip install pyyaml)", file=sys.stderr)
    raise SystemExit(3)

# scripts/ -> qmclaw-server -> Agentic Workflow -> repo root
_THIS = Path(__file__).resolve()
SERVER_ROOT = _THIS.parents[1]
REPO_ROOT = _THIS.parents[3]
REPO_SKILLS = REPO_ROOT / "skills"
DATA_SKILLS = SERVER_ROOT / "data" / "skills"
ENV_FILE = SERVER_ROOT / ".env"
DEFAULT_HERMES_HOME = SERVER_ROOT / "data" / "hermes"

# Windows reparse tags (junction / symlink). os.path.islink() is False for
# junctions on Python 3.11, so the tag is the only reliable signal there.
_TAG_MOUNT_POINT = 0xA0000003
_TAG_SYMLINK = 0xA000000C

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_BAD_LAYOUT = 2


def log(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(f"[warn] {msg}", flush=True)


def _norm(path: "os.PathLike[str] | str") -> str:
    """Case- and separator-insensitive comparison key for Windows paths."""
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _is_link_like(path: Path) -> bool:
    """True for a directory junction or a symlink (even a dangling one)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if os.path.islink(path):
        return True
    return getattr(st, "st_reparse_tag", 0) in (_TAG_MOUNT_POINT, _TAG_SYMLINK)


def _link_target(path: Path) -> str:
    """Best-effort resolved target of a junction/symlink, for logging only."""
    try:
        return str(Path(os.path.realpath(path)))
    except OSError:
        return "<unresolved>"


def resolve_hermes_home() -> tuple[Path, str]:
    """``(hermes_home, source)``: process env, then <server>/.env, then default."""
    raw = os.environ.get("HERMES_HOME", "").strip()
    if raw:
        return _as_path(raw), "process env HERMES_HOME"

    if ENV_FILE.is_file():
        try:
            from dotenv import dotenv_values

            values = dotenv_values(ENV_FILE, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - any dotenv/decoding trouble
            warn(f"could not read {ENV_FILE} ({exc.__class__.__name__}); using default")
        else:
            value = (values.get("HERMES_HOME") or "").strip()
            if value:
                return _as_path(value), f"{ENV_FILE.name} (HERMES_HOME)"

    return DEFAULT_HERMES_HOME, "built-in default <server>/data/hermes"


def _as_path(raw: str) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(raw))
    path = Path(expanded)
    return path if path.is_absolute() else (SERVER_ROOT / path)


def ensure_external_dir(config_path: Path) -> int:
    """Merge ``<repo>/skills`` into ``skills.external_dirs``; atomic write."""
    desired = str(REPO_SKILLS)

    raw_text: str | None = None
    if config_path.exists():
        try:
            raw_text = config_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"[fatal] cannot read {config_path}: {exc}", file=sys.stderr)
            return EXIT_CONFIG_ERROR
        try:
            loaded = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            print(f"[fatal] {config_path} is not valid YAML, refusing to overwrite:\n{exc}", file=sys.stderr)
            return EXIT_CONFIG_ERROR
        if loaded is None:
            config: Any = {}
        elif not isinstance(loaded, dict):
            print(
                f"[fatal] {config_path} top level is {type(loaded).__name__}, expected a mapping; "
                "refusing to overwrite",
                file=sys.stderr,
            )
            return EXIT_CONFIG_ERROR
        else:
            config = loaded
    else:
        config = {}

    skills = config.get("skills")
    if skills is None:
        skills = {}
        config["skills"] = skills
    elif not isinstance(skills, dict):
        print(
            f"[fatal] {config_path}: 'skills' is {type(skills).__name__}, expected a mapping; "
            "refusing to overwrite",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR

    changed = False
    entries = skills.get("external_dirs")
    if entries is None:
        entries = []
        skills["external_dirs"] = entries
        changed = True
    elif isinstance(entries, str):
        entries = [entries]
        skills["external_dirs"] = entries
        changed = True
    elif not isinstance(entries, list):
        print(
            f"[fatal] {config_path}: 'skills.external_dirs' is {type(entries).__name__}, "
            "expected a list; refusing to overwrite",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR

    if not any(isinstance(e, str) and e.strip() == desired for e in entries):
        entries.append(desired)
        changed = True
    else:  # collapse duplicate copies of our own entry, keep user order
        seen = False
        deduped: list[Any] = []
        for entry in entries:
            if isinstance(entry, str) and entry.strip() == desired:
                if seen:
                    changed = True
                    continue
                seen = True
            deduped.append(entry)
        entries = deduped
        skills["external_dirs"] = entries

    if not changed:
        log(f"[config] no change: {config_path} already lists {desired}")
        return EXIT_OK

    text = yaml.safe_dump(config, sort_keys=False, default_flow_style=False, allow_unicode=True)
    # A fixed, pid-scoped temp name next to the target (same volume, so os.replace
    # is atomic).  tempfile.mkstemp is deliberately avoided: on a write-denied
    # directory its retry loop spins TMP_MAX (~238k) times before giving up, which
    # looks like a hang instead of a clean permission error.
    tmp_path = config_path.with_name(f"{config_path.name}.{os.getpid()}.tmp")
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, config_path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as exc:
        print(f"[fatal] cannot write {config_path}: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    log(f"[config] updated {config_path}")
    if raw_text is None:
        log(f"[config]   created new file with skills.external_dirs = [{desired}]")
    else:
        log(f"[config]   skills.external_dirs += {desired}")
    return EXIT_OK


def ensure_data_skills_junction(link: Path = DATA_SKILLS, target: Path = REPO_SKILLS) -> None:
    """Make ``<server>/data/skills`` a junction to ``<repo>/skills`` (never fatal).

    ``link``/``target`` default to the real paths and exist as parameters so the
    behaviour below can be exercised directly.
    """
    if _is_link_like(link):
        resolved = _link_target(link)
        if _norm(resolved) == _norm(target):
            log(f"[junction] junction ok: {link} -> {resolved}")
        else:
            warn(f"{link} is a link to {resolved}, expected {target}; leaving it untouched")
        return

    if link.exists():
        if link.is_dir():
            try:
                entries = list(link.iterdir())
            except OSError as exc:
                warn(f"cannot inspect {link}: {exc}; leaving it untouched")
                return
            if entries:
                warn(
                    f"{link} is a real non-empty directory ({len(entries)} entries); "
                    "NOT deleting it. Move it aside and re-run to get the junction to "
                    f"{target}. The agent still sees the repo skills via config.yaml."
                )
                return
            try:
                link.rmdir()
            except OSError as exc:
                warn(f"cannot remove empty directory {link}: {exc}; skipping junction step")
                return
            log(f"[junction] removed empty directory {link}")
        else:
            warn(f"{link} exists and is not a directory; skipping junction step")
            return

    try:
        link.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        warn(f"cannot create {link.parent}: {exc}; skipping junction step")
        return

    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
    )
    stdout = proc.stdout.decode(errors="replace").strip()
    stderr = proc.stderr.decode(errors="replace").strip()
    if proc.returncode == 0 and _is_link_like(link):
        log(f"[junction] created: {link} -> {target}")
        if stdout:
            log(f"[junction]   mklink: {stdout}")
    else:
        warn(f"mklink /J {link} {target} failed (exit {proc.returncode})")
        if stderr:
            warn(f"mklink stderr: {stderr}")
        elif stdout:
            warn(f"mklink output: {stdout}")
        warn("the agent still reaches the repo skills through config.yaml external_dirs")


def main() -> int:
    log(f"[paths] repo root   = {REPO_ROOT}")
    log(f"[paths] server root = {SERVER_ROOT}")

    if not REPO_SKILLS.is_dir():
        print(f"[fatal] expected repo skills directory not found: {REPO_SKILLS}", file=sys.stderr)
        return EXIT_BAD_LAYOUT

    hermes_home, source = resolve_hermes_home()
    log(f"[paths] HERMES_HOME = {hermes_home}  (from {source})")
    config_path = hermes_home / "config.yaml"

    rc = ensure_external_dir(config_path)
    if rc != EXIT_OK:
        return rc

    ensure_data_skills_junction()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
