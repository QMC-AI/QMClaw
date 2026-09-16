"""
backends/__init__.py - Backend adapter layer for quantum measurement systems.

This module provides a unified interface for different quantum measurement
backends (LQCS, OpenSystemQ, Qiskit, etc.), enabling easy switching between
systems while maintaining a consistent API.

Usage:
    from backends import get_backend, create_backend

    # Create the current backend (from system.json)
    backend = create_backend()

    # Or get a specific backend
    backend = get_backend('lqcs')

    # Use the backend
    qubits = backend.get_qubits()
    experiments = backend.list_experiments()
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any, List

print("[backends] Module loading: starting...", file=sys.stderr, flush=True)
print(f"[backends] Module id = {id(sys.modules.get('backends', None))}", file=sys.stderr, flush=True)

from .base import BackendInterface, BackendMetadata
print("[backends] base.py imported", file=sys.stderr, flush=True)

from .backend_types import BackendStatus, QubitInfo, SessionConfig, ExperimentInfo
print("[backends] backend_types.py imported", file=sys.stderr, flush=True)

from .registry import (
    BackendRegistry,
    BackendFactory,
    get_factory,
    get_backend,
    create_backend as _create_backend,
    create_backend as create_backend,
)
print("[backends] registry.py imported", file=sys.stderr, flush=True)

# Initialize registry and discover backends
_registry = BackendRegistry.get_instance()
print("[backends] Registry instance created", file=sys.stderr, flush=True)

# Discover backends in this directory
_backends_dir = Path(__file__).parent
_registry.discover_backends(_backends_dir)
print(f"[backends] Discovered backends: {_registry.list_backends()}", file=sys.stderr, flush=True)


def load_system_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load system configuration from JSON file.

    Args:
        config_path: Path to system.json. If None, uses default location.

    Returns:
        Configuration dictionary
    """
    if config_path is None:
        config_path = _backends_dir.parent / "config" / "system.json"

    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    print("[backends load_system_config] Config not found, using default", file=sys.stderr, flush=True)
    return {"system": "lqcs"}


def init_backend(
    config_path: Optional[Path] = None,
    session_path: Optional[List[str]] = None,
    timeout: int = 60,
) -> BackendInterface:
    """Initialize the configured backend.

    This is the main entry point for initializing the backend system.
    It tries to use the legacy backend.py first, then falls back to the
    new LQCSBackend adapter.

    Args:
        config_path: Path to system.json configuration file
        session_path: Optional initial session path
        timeout: Maximum seconds to wait for initialization (default 60)

    Returns:
        Initialized backend instance
    """
    import sys
    import time as _time
    print("[backends init_backend] STARTING", file=sys.stderr, flush=True)

    # ── Method 1: Try to import legacy backend.py ───────────────────────────
    # This is the original method that worked before the backends adapter was added.
    # It directly imports from measure_scripts/backend.py which handles all initialization.
    print("[backends init_backend] Trying legacy backend.py import...", file=sys.stderr, flush=True)

    # Fix sys.path: Ray may have modified it, restore the correct paths
    # The correct paths should include measure_scripts/measure_scripts and sq_workflow
    # We need to find the QMClaw root (parent of "Agentic Workflow" folder or "measure_scripts")
    _backends_init_py_path = Path(__file__).parent  # .../qmclaw-server/backends
    _server_dir = _backends_init_py_path.parent  # .../qmclaw-server

    # Find QMClaw root by going up until we find measure_scripts
    _qmclaw_root = _server_dir
    for _i in range(10):
        _parent = _qmclaw_root.parent
        if not _parent or str(_parent) == str(_qmclaw_root):
            break
        _qmclaw_root = _parent
        if (_qmclaw_root / "measure_scripts").exists():
            break

    print(f"[backends init_backend] _qmclaw_root = {_qmclaw_root}", file=sys.stderr, flush=True)
    print(f"[backends init_backend] measure_scripts exists = {(_qmclaw_root / 'measure_scripts').exists()}", file=sys.stderr, flush=True)

    _sq_workflow_path = _qmclaw_root / "measure_scripts" / "measure_scripts" / "sq_workflow"
    _measure_scripts_path = _qmclaw_root / "measure_scripts" / "measure_scripts"

    print(f"[backends init_backend] _sq_workflow_path = {_sq_workflow_path}", file=sys.stderr, flush=True)
    print(f"[backends init_backend] _sq_workflow_path exists = {_sq_workflow_path.exists()}", file=sys.stderr, flush=True)
    print(f"[backends init_backend] backend.py exists = {(_sq_workflow_path / 'backend.py').exists()}", file=sys.stderr, flush=True)

    # Remove ray/thirdparty_files from sys.path and add our paths at the beginning
    # Create a new sys.path with correct paths at front
    _correct_paths = [str(_sq_workflow_path), str(_measure_scripts_path)]
    _new_sys_path = _correct_paths.copy()
    for _p in sys.path:
        if _p not in _correct_paths and 'ray/thirdparty_files' not in _p:
            _new_sys_path.append(_p)

    # Also keep ray path at end if it was there
    for _p in sys.path:
        if 'ray/thirdparty_files' in _p and _p not in _new_sys_path:
            _new_sys_path.append(_p)

    sys.path[:] = _new_sys_path
    print(f"[backends init_backend] sys.path[:5] after fix = {sys.path[:5]}", file=sys.stderr, flush=True)

    # Skip 'import backend' path - it hangs when Ray is already initialized
    # Instead, use LQCSBackend adapter directly which handles all initialization
    print("[backends init_backend] Skipping legacy 'import backend' (causes Ray conflict)", file=sys.stderr, flush=True)
    print("[backends init_backend] Using LQCSBackend adapter directly...", file=sys.stderr, flush=True)
    sys.stderr.flush()

    # ── Method 2: Use LQCSBackend adapter ────────────────────────────────
    import threading

    # Load configuration
    config = load_system_config(config_path)
    print(f"[backends init_backend] config loaded: {config}", file=sys.stderr, flush=True)

    # Get system name from config
    system_name = config.get("system", "lqcs")
    print(f"[backends init_backend] system_name: {system_name}", file=sys.stderr, flush=True)

    # Set current backend in registry
    if system_name not in _registry.list_backends():
        print(
            f"WARNING: Backend '{system_name}' not found, falling back to 'lqcs'",
            file=sys.stderr,
        )
        system_name = "lqcs"

    _registry.set_current(system_name)

    # Create backend
    print(f"[backends init_backend] Creating backend '{system_name}'...", file=sys.stderr, flush=True)
    backend = _create_backend(system_name, config)
    print(f"[backends init_backend] Backend created: {type(backend)}", file=sys.stderr, flush=True)

    # Initialize with session path - with overall timeout protection
    print(f"[backends init_backend] Initializing with session_path={session_path}...", file=sys.stderr, flush=True)
    sys.stderr.flush()

    # Wrap initialization in a thread with timeout
    result = {"init_result": None, "init_error": None}

    def _init_with_timeout():
        try:
            print("[backends init_backend] Thread: calling backend.initialize()...", file=sys.stderr, flush=True)
            sys.stderr.flush()
            result["init_result"] = backend.initialize(session_path)
            print("[backends init_backend] Thread: backend.initialize() returned!", file=sys.stderr, flush=True)
        except Exception as e:
            import traceback
            result["init_error"] = e
            print(f"[backends init_backend] Thread: backend.initialize() raised: {e}", file=sys.stderr, flush=True)
            print(f"[backends init_backend] Thread traceback: {traceback.format_exc()}", file=sys.stderr, flush=True)
        sys.stderr.flush()

    init_thread = threading.Thread(target=_init_with_timeout)
    init_thread.daemon = True
    init_thread.start()
    print(f"[backends init_backend] Thread started, waiting up to {timeout}s...", file=sys.stderr, flush=True)
    sys.stderr.flush()
    init_thread.join(timeout=timeout)

    print(f"[backends init_backend] Thread join completed, is_alive={init_thread.is_alive()}", file=sys.stderr, flush=True)
    sys.stderr.flush()

    if init_thread.is_alive():
        print(f"[backends init_backend] TIMEOUT after {timeout} seconds!", file=sys.stderr, flush=True)
        sys.stderr.flush()
        raise TimeoutError(f"Backend initialization timed out after {timeout} seconds")
    elif result["init_error"]:
        print(f"[backends init_backend] initialization error: {result['init_error']}", file=sys.stderr, flush=True)
        raise result["init_error"]

    print(f"[backends init_backend] Initialize returned: {result['init_result']}, status={backend.status}", file=sys.stderr, flush=True)

    return backend


def switch_system(
    system_name: str,
    config_path: Optional[Path] = None,
) -> BackendInterface:
    """Switch to a different measurement system.

    Args:
        system_name: Name of the backend system (e.g., 'lqcs', 'qiskit')
        config_path: Path to system.json

    Returns:
        New backend instance for the specified system
    """
    # Load config and set current system
    config = load_system_config(config_path)
    config["system"] = system_name

    # Save new config
    if config_path is None:
        config_path = _backends_dir.parent / "config" / "system.json"
    _registry.save_config(config_path, system_name)

    # Shutdown current backend
    current = _registry.current
    if current and current.instance:
        current.instance.shutdown()

    # Set and create new backend
    _registry.set_current(system_name)
    return _create_backend(system_name, config)


# Re-export for convenience
__all__ = [
    # Core interfaces
    "BackendInterface",
    "BackendMetadata",
    "BackendStatus",
    "SessionConfig",
    "QubitInfo",
    "ExperimentInfo",
    # Registry functions
    "BackendRegistry",
    "BackendFactory",
    "get_factory",
    "get_backend",
    "create_backend",
    # Convenience functions
    "load_system_config",
    "init_backend",
    "switch_system",
]
