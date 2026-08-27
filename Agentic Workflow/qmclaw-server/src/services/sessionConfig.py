"""
Session Configuration Manager

Reads and writes session configuration from JSON file.
Session path format: ['', user, ...path_segments]
"""

import json
import os
from pathlib import Path

# Config file location
CONFIG_DIR = Path(__file__).parent.parent / "config"
CONFIG_FILE = CONFIG_DIR / "session.json"

def _ensure_config_dir():
    """Ensure config directory exists."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def get_session_config() -> dict:
    """
    Get current session configuration.

    Returns:
        dict with 'user' and 'path' keys
        path is a list of path segments (e.g., ['test', '20260324'])
    """
    _ensure_config_dir()

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            return config.get('session', {'user': 'LQHL', 'path': ['test', '20260324']})
        except (json.JSONDecodeError, IOError):
            pass

    # Default config
    return {'user': 'LQHL', 'path': ['test', '20260324']}

def set_session_config(user: str, path: list) -> dict:
    """
    Set session configuration and save to file.

    Args:
        user: User name (e.g., 'LQHL')
        path: List of path segments (e.g., ['test', '20260324'])

    Returns:
        The saved configuration
    """
    _ensure_config_dir()

    config = {'session': {'user': user, 'path': path}}

    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    return config['session']

def get_session_path() -> list:
    """
    Get full session path for LabRAD.

    Returns:
        List like ['', 'LQHL', 'test', '20260324']
    """
    config = get_session_config()
    return ['', config['user']] + config['path']

def set_session_path(user: str, path: list) -> list:
    """
    Set session path and return full path.

    Args:
        user: User name
        path: Path segments

    Returns:
        Full path like ['', 'LQHL', 'test', '20260324']
    """
    set_session_config(user, path)
    return get_session_path()
