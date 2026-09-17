"""
backends/registry.py - Backend registry and configuration management.

This module provides the BackendRegistry and BackendFactory classes for
managing and creating backend instances.
"""

import os
import json
from pathlib import Path
from typing import Dict, Optional, Type, List, Any

from .base import BackendInterface, BackendMetadata
from .backend_types import BackendStatus


class BackendEntry:
    """Registry entry for a backend."""

    def __init__(
        self,
        name: str,
        module_path: str,
        class_name: str,
        metadata: Optional[BackendMetadata] = None,
    ):
        self.name = name
        self.module_path = module_path
        self.class_name = class_name
        self.metadata = metadata
        self.instance: Optional[BackendInterface] = None
        self.status = BackendStatus.UNINITIALIZED


class BackendRegistry:
    """Central registry for all available backends."""

    _instance: Optional["BackendRegistry"] = None

    def __init__(self):
        self._backends: Dict[str, BackendEntry] = {}
        self._current: Optional[str] = None

    @classmethod
    def get_instance(cls) -> "BackendRegistry":
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = BackendRegistry()
        return cls._instance

    def register(
        self,
        name: str,
        module_path: str,
        class_name: str,
        metadata: Optional[BackendMetadata] = None,
    ) -> None:
        """Register a backend implementation."""
        self._backends[name] = BackendEntry(
            name=name,
            module_path=module_path,
            class_name=class_name,
            metadata=metadata,
        )

    def get(self, name: str) -> Optional[BackendEntry]:
        """Get a backend entry by name."""
        return self._backends.get(name)

    def list_backends(self) -> List[str]:
        """List all registered backend names."""
        return list(self._backends.keys())

    def set_current(self, name: str) -> None:
        """Set the current active backend."""
        if name not in self._backends:
            raise ValueError(f"Unknown backend: {name}")
        self._current = name

    @property
    def current(self) -> Optional[BackendEntry]:
        """Get current backend entry."""
        if self._current is None:
            return None
        return self._backends.get(self._current)

    @property
    def current_name(self) -> Optional[str]:
        """Get current backend name."""
        return self._current

    def load_config(self, config_path: Path) -> Dict[str, Any]:
        """Load backend configuration from JSON file."""
        if not config_path.exists():
            return {"system": "lqcs"}

        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        # Set current backend from config
        system_name = config.get("system", "lqcs")
        if system_name in self._backends:
            self._current = system_name

        return config

    def save_config(self, config_path: Path, system_name: str, **kwargs) -> None:
        """Save backend configuration to JSON file."""
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config = {"system": system_name, **kwargs}
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def discover_backends(self, backends_dir: Path) -> None:
        """Auto-discover backends in the backends directory."""
        if not backends_dir.exists():
            return

        # Known class names for built-in backends
        known_classes = {
            "lqcs": "LQCSBackend",
            "opensystemq": "OpensystemqBackend",
            "qiskit": "QiskitBackend",
        }

        for entry in backends_dir.iterdir():
            if entry.is_dir() and not entry.name.startswith("_"):
                # Skip if already registered (explicit registration takes precedence)
                if entry.name in self._backends:
                    continue
                init_file = entry / "__init__.py"
                if init_file.exists():
                    class_name = known_classes.get(
                        entry.name,
                        f"{entry.name.title().replace('_', '')}Backend",
                    )
                    self.register(
                        name=entry.name,
                        module_path=f"backends.{entry.name}",
                        class_name=class_name,
                    )


class BackendFactory:
    """Factory for creating and managing backend instances."""

    def __init__(self):
        self._registry = BackendRegistry.get_instance()

    def create_backend(
        self,
        name: Optional[str] = None,
        config: Optional[Dict] = None,
    ) -> BackendInterface:
        """Create a backend instance by name.

        Args:
            name: Backend name (uses current if None)
            config: Optional configuration dict

        Returns:
            Backend instance implementing BackendInterface
        """
        # Determine which backend to use
        if name is None:
            entry = self._registry.current
            if entry is None:
                raise RuntimeError("No backend selected")
        else:
            entry = self._registry.get(name)
            if entry is None:
                # Try to discover/register this backend first
                self._try_register(name)
                entry = self._registry.get(name)
                if entry is None:
                    raise ValueError(f"Unknown backend: {name}")

        # Import and instantiate
        import importlib

        # Ensure the module is imported (triggers _register_backend if not already done)
        importlib.import_module(entry.module_path)

        module = importlib.import_module(entry.module_path)
        backend_class = getattr(module, entry.class_name)

        # Create instance with config
        instance = backend_class(config or {})

        # Update registry
        entry.instance = instance
        entry.status = BackendStatus.INITIALIZING

        return instance

    def _try_register(self, name: str) -> None:
        """Try to register a backend by name (imports the module)."""
        import importlib
        try:
            module = importlib.import_module(f"backends.{name}")
            # Module import should trigger its _register_backend()
        except ImportError:
            pass

    def get_backend(self, name: Optional[str] = None) -> Optional[BackendInterface]:
        """Get an existing backend instance."""
        if name is None:
            entry = self._registry.current
        else:
            entry = self._registry.get(name)

        if entry is None:
            return None
        return entry.instance


# Convenience functions
def get_factory() -> BackendFactory:
    """Get global backend factory."""
    return BackendFactory()


def get_backend(name: Optional[str] = None) -> Optional[BackendInterface]:
    """Convenience function to get backend."""
    return get_factory().get_backend(name)


def create_backend(
    name: Optional[str] = None,
    config: Optional[Dict] = None,
) -> BackendInterface:
    """Convenience function to create backend."""
    return get_factory().create_backend(name, config)
