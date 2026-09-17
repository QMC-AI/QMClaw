"""
backends/base.py - Abstract interfaces for quantum measurement backends.

This module defines the Protocol classes that all backend implementations
must conform to, ensuring a consistent API across different measurement systems.
"""

from dataclasses import dataclass, field

from typing import Protocol, Any, Dict, List, Optional, runtime_checkable


@runtime_checkable
class ConnectionInterface(Protocol):
    """Protocol for hardware connection management."""

    @property
    def host(self) -> str:
        """Connection host address."""
        ...

    @property
    def port(self) -> int:
        """Connection port."""
        ...

    @property
    def connected(self) -> bool:
        """Whether currently connected."""
        ...

    def disconnect(self) -> None:
        """Close the connection."""
        ...


@runtime_checkable
class DataLabInterface(Protocol):
    """Protocol for data storage operations."""

    @property
    def data(self) -> Any:
        """Raw data array."""
        ...

    @property
    def parameters(self) -> Dict[str, Any]:
        """Dataset parameters."""
        ...

    @property
    def dataset_name(self) -> str:
        """Current dataset name."""
        ...

    @property
    def session(self) -> List[str]:
        """Current session path."""
        ...

    def loadDataset(self, idx: int) -> None:
        """Load a dataset by index (-1 for latest)."""
        ...


@runtime_checkable
class SessionInterface(Protocol):
    """Protocol for session/registry management."""

    def keys(self) -> List[str]:
        """List all registered keys (qubits, parameters)."""
        ...

    def __contains__(self, key: str) -> bool:
        """Check if key exists."""
        ...

    def __getitem__(self, key: str) -> Any:
        """Get item by key."""
        ...

    def __setitem__(self, key: str, value: Any) -> None:
        """Set item by key."""
        ...


@runtime_checkable
class BackendInterface(Protocol):
    """Main backend interface - aggregates all sub-interfaces."""

    # ── Connection ──────────────────────────────────────────────────────────────

    @property
    def cxn(self) -> Any:
        """Hardware connection (e.g., LabRAD connection)."""
        ...

    @property
    def connected(self) -> bool:
        """Whether the backend is connected."""
        ...

    # ── Session ───────────────────────────────────────────────────────────────

    @property
    def s(self) -> Any:
        """Session manager for qubit parameters."""
        ...

    @property
    def current_session_path(self) -> List[str]:
        """Current session path."""
        ...

    # ── Experiments ────────────────────────────────────────────────────────────

    @property
    def sq(self) -> Any:
        """Module containing experiment functions (sq.spectroscopy, sq.t1, etc.)."""
        ...

    def list_experiments(self) -> List[Dict[str, str]]:
        """List all available experiment functions."""
        ...

    # ── Data ────────────────────────────────────────────────────────────────

    @property
    def data(self) -> Any:
        """DataLab instance for data storage."""
        ...

    @property
    def qter(self) -> Any:
        """Data analysis tools (fitting, metrics extraction)."""
        ...

    # ── Initialization ──────────────────────────────────────────────────────

    def initialize(self, session_path: Optional[List[str]] = None) -> bool:
        """Initialize backend with optional session path."""
        ...

    def switch_session(self, session_path: List[str]) -> bool:
        """Switch to a different session."""
        ...

    def reload_qubits(self, session_path: Optional[List[str]] = None) -> bool:
        """Reload qubit objects for the session."""
        ...

    def shutdown(self) -> None:
        """Clean shutdown of backend resources."""
        ...

    # ── Qubit Management ───────────────────────────────────────────────────

    def get_qubits(self) -> List[Dict[str, Any]]:
        """Get list of available qubits."""
        ...

    def get_qubit(self, name: str) -> Optional[Any]:
        """Get a specific qubit object by name."""
        ...


@dataclass
class BackendMetadata:
    """Metadata about a backend implementation."""

    name: str = ""
    description: str = ""
    version: str = "0.0.0"
    required_packages: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BackendMetadata":
        """Create from dictionary."""
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            version=data.get("version", "0.0.0"),
            required_packages=data.get("required_packages", []),
        )
