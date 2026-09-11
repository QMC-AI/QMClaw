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
    It loads the configuration from system.json and creates the
    appropriate backend instance.

    Args:
        config_path: Path to system.json configuration file
        session_path: Optional initial session path
        timeout: Maximum seconds to wait for initialization (default 60)

    Returns:
        Initialized backend instance
    """
    import sys
    import threading
    print("[backends init_backend] STARTING", file=sys.stderr, flush=True)
    print(f"[backends init_backend] backends in sys.modules: {'backends' in sys.modules}", file=sys.stderr, flush=True)
    print(f"[backends init_backend] backends module id: {id(sys.modules.get('backends', None))}", file=sys.stderr, flush=True)

    # Load configuration
    print("[backends init_backend] calling load_system_config()...", file=sys.stderr, flush=True)
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
    print("[backends init_backend] calling _create_backend()...", file=sys.stderr, flush=True)
    backend = _create_backend(system_name, config)
    print(f"[backends init_backend] Backend created: {type(backend)}", file=sys.stderr, flush=True)

    # Initialize with session path - with overall timeout protection
    print(f"[backends init_backend] Initializing with session_path={session_path}...", file=sys.stderr, flush=True)
    print("[backends init_backend] calling backend.initialize()...", file=sys.stderr, flush=True)

    # Wrap initialization in a thread with timeout
    result = {"init_result": None, "init_error": None}

    def _init_with_timeout():
        try:
            result["init_result"] = backend.initialize(session_path)
        except Exception as e:
            result["init_error"] = e

    init_thread = threading.Thread(target=_init_with_timeout)
    init_thread.daemon = True
    init_thread.start()
    init_thread.join(timeout=timeout)

    if init_thread.is_alive():
        print(f"[backends init_backend] TIMEOUT after {timeout} seconds!", file=sys.stderr, flush=True)
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
