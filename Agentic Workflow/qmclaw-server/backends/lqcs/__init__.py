"""
backends/lqcs/__init__.py - LQCS (Lab Quantum Control System) backend.

This backend implements the BackendInterface using the lqms library
for connection to LabRAD-based measurement hardware.
"""

import os
import sys
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional

from ..base import BackendInterface, BackendMetadata
from ..backend_types import BackendStatus, QubitInfo


# Module-level lock for thread safety
_labrad_lock = threading.Lock()


class LQCSBackend:
    """LQCS backend implementation using lqms/LabRAD."""

    # Backend metadata
    METADATA = BackendMetadata(
        name="lqcs",
        description="Lab Quantum Control System - LabRAD-based measurement backend",
        version="1.0.0",
        required_packages=["lqms", "labrad", "pylab"],
    )

    def __init__(self, config: Dict[str, Any]):
        """Initialize LQCS backend.

        Args:
            config: Configuration dictionary from system.json
        """
        self._config = config
        self._status = BackendStatus.UNINITIALIZED

        # Core components - initialized in initialize()
        self._cxn: Optional[Any] = None
        self._s: Optional[Any] = None
        self._sq: Optional[Any] = None
        self._data: Optional[Any] = None
        self._qter: Optional[Any] = None
        self._BasicTuner: Optional[Any] = None
        self._generate_qubit: Optional[Any] = None
        self._generate_coupler: Optional[Any] = None
        self._all_qubits: Dict[str, Any] = {}
        self._all_couplers: Dict[str, Any] = {}

        # Session tracking
        self._current_session_path: List[str] = []
        self._session_config_file: Optional[Path] = None

        # Add lqms to path if needed
        self._setup_paths()

    def _setup_paths(self) -> None:
        """Setup Python paths for lqms imports."""
        # Get paths from config or environment
        # Default root is two levels up from backends/lqcs/
        default_root = Path(__file__).parent.parent.parent.parent.parent
        if not default_root.exists():
            default_root = Path(__file__).parent.parent.parent.parent

        measure_scripts = os.environ.get(
            "MEASURE_SCRIPTS",
            default_root / "measure_scripts" / "measure_scripts",
        )
        backend_dir = os.environ.get(
            "BACKEND_DIR",
            measure_scripts / "sq_workflow",
        )

        for path in [str(measure_scripts), str(backend_dir)]:
            if path not in sys.path and os.path.exists(path):
                sys.path.insert(0, path)

    # ── BackendInterface Implementation ──────────────────────────────────────

    @property
    def status(self) -> BackendStatus:
        """Get current backend status."""
        return self._status

    @property
    def cxn(self) -> Any:
        """Get LabRAD connection."""
        return self._cxn

    @property
    def connected(self) -> bool:
        """Check if connected to LabRAD."""
        return self._cxn is not None and self._status == BackendStatus.READY

    @property
    def s(self) -> Any:
        """Get session manager."""
        return self._s

    @property
    def sq(self) -> Any:
        """Get experiment module."""
        return self._sq

    @property
    def data(self) -> Any:
        """Get DataLab instance."""
        return self._data

    @property
    def qter(self) -> Any:
        """Get analysis tools."""
        return self._qter

    @property
    def lock(self) -> threading.Lock:
        """Get thread lock for thread-safe operations."""
        return _labrad_lock

    @property
    def current_session_path(self) -> List[str]:
        """Get current session path."""
        return self._current_session_path

    def initialize(self, session_path: Optional[List[str]] = None) -> bool:
        """Initialize the LQCS backend.

        Args:
            session_path: Optional session path like ['', 'LQHL', 'test', '20260324']

        Returns:
            True if initialization successful
        """
        if self._status == BackendStatus.READY:
            return True

        self._status = BackendStatus.INITIALIZING

        try:
            # Import lqms components
            print("[LQCS Backend] Importing labrad...", file=sys.stderr, flush=True)
            import labrad
            print("[LQCS Backend] labrad imported", file=sys.stderr, flush=True)
            print("[LQCS Backend] Importing lqms modules...", file=sys.stderr, flush=True)
            from lqms.utils.save_path import get_info_path
            from lqms.data_process import dataAnalysisCore as dc, QubitUpdater
            print("[LQCS Backend]   imported get_info_path, dc, QubitUpdater", file=sys.stderr, flush=True)
            from lqms.measure import generate_qubit, generate_coupler
            print("[LQCS Backend]   imported generate_qubit, generate_coupler", file=sys.stderr, flush=True)
            from lqms.measure.basic import BasicTuner, util
            print("[LQCS Backend]   imported BasicTuner, util", file=sys.stderr, flush=True)
            from lqms.measure.tuners import sq_nodes as sq_module
            print("[LQCS Backend]   imported sq_nodes", file=sys.stderr, flush=True)

            print("[LQCS Backend] All imports done, about to connect to LabRAD...", file=sys.stderr, flush=True)
            # Store generate functions for later use
            self._generate_qubit = generate_qubit
            self._generate_coupler = generate_coupler

            # Connect to LabRAD
            print("[LQCS Backend] About to call labrad.connect()...", file=sys.stderr, flush=True)
            print("[LQCS Backend] NOTE: If this hangs, LabRAD server may not be running!", file=sys.stderr, flush=True)
            sys.stderr.flush()
            print("[LQCS Backend] Calling labrad.connect() now...", file=sys.stderr, flush=True)
            sys.stderr.flush()
            self._cxn = labrad.connect()
            print("[LQCS Backend] LabRAD connected! Connection ID:", self._cxn.ID if hasattr(self._cxn, 'ID') else 'unknown', file=sys.stderr, flush=True)
            print("[LQCS Backend] Calling util.setWiringInfo()...", file=sys.stderr, flush=True)
            util.setWiringInfo(self._cxn)
            print("[LQCS Backend] setWiringInfo done", file=sys.stderr, flush=True)

            # Determine session configuration
            if session_path is None:
                session_path = self._load_session_config()

            self._current_session_path = session_path
            print(f"[LQCS Backend] session_path = {session_path}", file=sys.stderr, flush=True)

            # Create session switcher with timeout protection
            import signal
            user = session_path[1] if len(session_path) > 1 else "LQHL"
            print(f"[LQCS Backend] Creating session switcher for user = {user}...", file=sys.stderr, flush=True)

            # Wrap switchSession with timeout using threading
            result = {"session": None, "error": None}
            def _switch_with_timeout():
                try:
                    from lqms.pyle.workflow import switchSession
                    result["session"] = switchSession(self._cxn, user=user)
                except Exception as e:
                    result["error"] = e

            switch_thread = threading.Thread(target=_switch_with_timeout)
            switch_thread.daemon = True
            switch_thread.start()
            switch_thread.join(timeout=30)  # 30 second timeout

            if switch_thread.is_alive():
                print("[LQCS Backend] switchSession TIMEOUT after 30 seconds!", file=sys.stderr, flush=True)
                raise TimeoutError("switchSession timed out after 30 seconds")
            elif result["error"]:
                raise result["error"]
            else:
                self._s = result["session"]
            print("[LQCS Backend] switchSession done", file=sys.stderr, flush=True)

            # Initialize data lab
            print("[LQCS Backend] Initializing DataLab...", file=sys.stderr, flush=True)
            dv = self._cxn.data_vault
            self._data = dc.DataLab(session_path, dv, dv_type="data_vault")
            # Load or create info - SKIP for now to avoid hanging on HDF5
            print("[LQCS Backend] Loading info (SKIPPING to avoid hang)...", file=sys.stderr, flush=True)
            print("[LQCS Backend]   calling get_info_path...", file=sys.stderr, flush=True)
            info_path = get_info_path(self._s)
            print(f"[LQCS Backend]   get_info_path returned: {info_path}", file=sys.stderr, flush=True)
            print("[LQCS Backend]   skipping InfoBase loading to avoid hang", file=sys.stderr, flush=True)
            info = None

            # Initialize analysis tools
            print("[LQCS Backend] Initializing QubitUpdater...", file=sys.stderr, flush=True)
            print("[LQCS Backend]   calling QubitUpdater()...", file=sys.stderr, flush=True)
            self._qter = QubitUpdater(self._data, info)
            print("[LQCS Backend] QubitUpdater initialized", file=sys.stderr, flush=True)

            # Generate qubits
            print("[LQCS Backend] Generating qubits...", file=sys.stderr, flush=True)
            self._all_qubits = generate_qubit(
                {"s": self._s}, info=info, sample=self._s
            )
            print("[LQCS Backend] generate_qubit done", file=sys.stderr, flush=True)

            # Generate couplers
            print("[LQCS Backend] Generating couplers...", file=sys.stderr, flush=True)
            self._all_couplers = generate_coupler(
                {"s": self._s}, info=info, sample=self._s
            )
            print("[LQCS Backend] generate_coupler done", file=sys.stderr, flush=True)

            # No need to inject - switchSession loads qubits automatically

            # Initialize BasicTuner
            auto_config = {
                "stats": 300,
                "correctX": False,
                "correctZ": False,
                "reset": False,
                "apply_21": False,
                "run_mode": "local",
            }
            self._BasicTuner = BasicTuner(**auto_config)
            # Set on CLASS for experiment functions to access (like original backend.py)
            BasicTuner._sample = self._s
            BasicTuner._all_qobjs = self._all_qubits | self._all_couplers

            # Get experiment module
            self._sq = sq_module

            self._status = BackendStatus.READY
            print(
                f"INIT: LQCS Backend ready — session={session_path}",
                file=sys.stderr,
                flush=True,
            )
            return True

        except Exception as e:
            self._status = BackendStatus.ERROR
            print(f"INIT ERROR: {e}", file=sys.stderr)
            return False

    def switch_session(self, session_path: List[str]) -> bool:
        """Switch to a different session.

        Args:
            session_path: New session path like ['', 'LQHL', 'test', '20260324']

        Returns:
            True if switch successful
        """
        with _labrad_lock:
            try:
                # Update DataVault directory
                dv = self._cxn.data_vault
                dv.cd("")  # absolute: go to root first
                clean_path = (
                    session_path[1:] if session_path and session_path[0] == "" else session_path
                )
                dv.cd(clean_path)

                # Update session switcher
                user = clean_path[0] if clean_path else "LQHL"
                self._s = switchSession(self._cxn, user=user)

                # Reload DataLab
                from lqms.data_process import dataAnalysisCore as dc

                self._data = dc.DataLab(session_path, dv, dv_type="data_vault")

                # Update qter reference
                if self._qter is not None:
                    self._qter.data = self._data

                # Reload qubits
                self.reload_qubits(session_path)

                self._current_session_path = session_path
                return True

            except Exception as e:
                print(f"Session switch failed: {e}", file=sys.stderr)
                return False

    def reload_qubits(self, session_path: Optional[List[str]] = None) -> bool:
        """Reload qubit objects for the session.

        Args:
            session_path: Session path (uses current if None)

        Returns:
            True if reload successful
        """
        if session_path is None:
            session_path = self._current_session_path

        try:
            from lqms.pyle.workflow import switchSession
            from lqms.utils.save_path import get_info_path
            from lqms.data_process import dataAnalysisCore as dc
            from lqms.measure import generate_qubit, generate_coupler

            # Create new session switcher
            user = session_path[1] if len(session_path) > 1 else "LQHL"
            self._s = switchSession(self._cxn, user=user)

            # Reload info and data lab
            info_path = get_info_path(self._s)
            info = dc.InfoBase(info_path) if os.path.exists(info_path) else None

            self._data = dc.DataLab(session_path, self._cxn.data_vault, dv_type="data_vault")

            # Update qter
            if self._qter is not None:
                self._qter.data = self._data

            # Regenerate qubits (switchSession already loads qubits)
            if self._generate_qubit:
                self._all_qubits = self._generate_qubit(
                    {"s": self._s}, info=info, sample=self._s
                )
                self._all_couplers = self._generate_coupler(
                    {"s": self._s}, info=info, sample=self._s
                )

                # Update BasicTuner class attributes (for experiment functions to access)
                if self._BasicTuner:
                    BasicTuner._sample = self._s
                    BasicTuner._all_qobjs = self._all_qubits | self._all_couplers

            self._current_session_path = session_path
            return True

        except Exception as e:
            print(f"Qubit reload failed: {e}", file=sys.stderr)
            return False

    def shutdown(self) -> None:
        """Clean shutdown of backend resources."""
        try:
            if self._cxn is not None:
                self._cxn.disconnect()
        except Exception as e:
            print(f"Error during shutdown: {e}", file=sys.stderr)
        finally:
            self._status = BackendStatus.SHUTDOWN

    # ── Convenience Methods ───────────────────────────────────────────────────

    def get_qubits(self) -> List[Dict[str, Any]]:
        """Get list of available qubits."""
        qubits = []
        if self._s:
            for qname in sorted(self._s.keys()):
                if qname.startswith("q"):
                    try:
                        qobj = self._s[qname]
                        qubit_info: Dict[str, Any] = {"name": qname}
                        if hasattr(qobj, "regs"):
                            try:
                                qubit_info["f10"] = float(qobj.regs.f10)
                                qubit_info["fread"] = float(qobj.regs.fread)
                                qubit_info["bias_z"] = float(qobj.regs.bias_z)
                                qubit_info["f21"] = float(qobj.regs.f21)
                                qubit_info["fc"] = float(qobj.regs.fc)
                            except Exception:
                                pass
                        qubits.append(qubit_info)
                    except Exception:
                        qubits.append({"name": qname})
        return qubits

    def get_qubit(self, name: str) -> Optional[Any]:
        """Get a specific qubit object by name."""
        if self._s and name in self._s:
            return self._s[name]
        return None

    def list_experiments(self) -> List[Dict[str, str]]:
        """List all available experiment functions."""
        experiments = []
        if self._sq:
            for name in dir(self._sq):
                if name.startswith("_") or name.startswith("qq"):
                    continue
                obj = getattr(self._sq, name)
                if not callable(obj):
                    continue
                doc = getattr(obj, "__doc__", None) or ""
                experiments.append({
                    "name": name,
                    "fullName": f"sq.{name}",
                    "doc": doc.strip().split("\n")[0][:120] if doc else "",
                })
        return sorted(experiments, key=lambda x: x["name"])

    def _load_session_config(self) -> List[str]:
        """Load session config from file."""
        if self._session_config_file is None:
            self._session_config_file = (
                Path(__file__).parent.parent.parent / "config" / "session.json"
            )

        if self._session_config_file.exists():
            try:
                with open(self._session_config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                session = config.get("session", {})
                user = session.get("user", "LQHL")
                path = session.get("path", ["test", "20260324"])
                return ["", user] + path
            except Exception:
                pass
        return ["", "LQHL", "test", "20260324"]

    # ── Backward Compatibility Properties ──────────────────────────────────

    @property
    def BasicTuner(self) -> Any:
        """Get BasicTuner class (for backward compatibility)."""
        return self._BasicTuner

    @property
    def generate_qubit(self) -> Any:
        """Get generate_qubit function (for backward compatibility)."""
        return self._generate_qubit


# Register with global registry
def _register_backend():
    registry = BackendRegistry.get_instance()
    registry.register(
        name="lqcs",
        module_path="backends.lqcs",
        class_name="LQCSBackend",
        metadata=LQCSBackend.METADATA,
    )


# Auto-register on import
from ..registry import BackendRegistry

_register_backend()


# Import json for session config loading
import json
