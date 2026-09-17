"""
backends/types.py - Shared type definitions for backend implementations.

This module defines enums, dataclasses, and type aliases used across
the backend adapter system.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum


class BackendStatus(Enum):
    """Backend lifecycle states."""
    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    READY = "ready"
    ERROR = "error"
    SHUTDOWN = "shutdown"


@dataclass
class QubitInfo:
    """Information about a qubit."""
    name: str
    f10: Optional[float] = None
    fread: Optional[float] = None
    bias_z: Optional[float] = None
    f21: Optional[float] = None
    fc: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionConfig:
    """Session configuration."""
    user: str
    path: List[str]  # e.g., ["test", "20260324"]

    @property
    def full_path(self) -> List[str]:
        """Get full path with root."""
        return ['', self.user] + self.path


@dataclass
class ExperimentInfo:
    """Information about an experiment function."""
    name: str
    full_name: str
    description: str = ""
    default_params: Dict[str, Any] = field(default_factory=dict)
    metrics: List[str] = field(default_factory=list)
