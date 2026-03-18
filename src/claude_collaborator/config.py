"""
Configuration management for claude-collaborator

Supports multiple configuration sources with priority:
1. Environment variables
2. .claude/config.json (project root)
3. .claude-collaborator.json (project root)
4. .claude-collaborator/config.json (home directory)
5. Auto-detection (.sln or .git search)
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


class Config:
    """Configuration manager for claude-collaborator"""

    # Config file names to search (in priority order)
    CONFIG_FILES = [
        ".claude/config.json",           # Claude's standard
        ".claude-collaborator.json",     # Legacy support
    ]

    # Home directory config
    HOME_CONFIG_PATH = Path.home() / ".claude-collaborator" / "config.json"

    # Environment variable names
    ENV_VARS = {
        "codebase_path": ["CSHARP_CODEBASE_PATH", "CODEBASE_PATH"],
        "glm_api_key": ["GLM_API_KEY"],
        "glm_model": ["GLM_MODEL"],
        "memory_path": ["MEMORY_PATH"],
    }

    def __init__(self, working_dir: Path = None):
        """
        Initialize configuration

        Args:
            working_dir: Working directory to search for config files
        """
        self.working_dir = Path(working_dir or Path.cwd())
        self._config: Dict[str, Any] = {}
        self._load_all()

    def _load_all(self):
        """Load configuration from all sources"""
        # Start with defaults
        self._config = {
            "glm_model": "glm-4.7",
            "memory_path": ".codebase-memory",
        }

        # Load from home config (global defaults)
        self._load_from_file(self.HOME_CONFIG_PATH)

        # Load from project config (override home)
        for config_file in self.CONFIG_FILES:
            config_path = self._find_project_file(config_file)
            if config_path:
                self._load_from_file(config_path)
                self._project_config_path = config_path
                break

        # Load from environment (override files)
        self._load_from_env()

    def _find_project_file(self, filename: str) -> Optional[Path]:
        """
        Find a file in the project by searching upward

        Args:
            filename: Name of file to find

        Returns:
            Path to file if found, None otherwise
        """
        current = self.working_dir
        while current != current.parent:  # Stop at filesystem root
            file_path = current / filename
            if file_path.exists():
                return file_path
            current = current.parent
        return None

    def _load_from_file(self, config_path: Path):
        """Load configuration from a JSON file"""
        if not config_path.exists():
            return

        try:
            with open(config_path, 'r') as f:
                file_config = json.load(f)
                self._config.update(file_config)
        except (json.JSONDecodeError, IOError):
            pass

    def _load_from_env(self):
        """Load configuration from environment variables"""
        for key, env_names in self.ENV_VARS.items():
            for env_name in env_names:
                value = os.getenv(env_name)
                if value is not None:
                    self._config[key] = value
                    break

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value"""
        return self._config.get(key, default)

    @property
    def codebase_path(self) -> Path:
        """Get the codebase path, with auto-detection fallback"""
        path_str = self.get("codebase_path")

        if path_str:
            path = Path(path_str)
            if path.is_absolute():
                return path
            # Relative to working directory
            return self.working_dir / path

        # Auto-detect: search for .sln or .git
        return self._auto_detect_codebase()

    def _auto_detect_codebase(self) -> Path:
        """
        Auto-detect codebase root by searching for .sln or .git

        Returns:
            Path to detected codebase root
        """
        current = self.working_dir

        while current != current.parent:
            # Check for .sln file (C# solution)
            sln_files = list(current.glob("*.sln"))
            if sln_files:
                return current

            # Check for .git directory (git repo root)
            if (current / ".git").exists():
                return current

            current = current.parent

        # Fallback to working directory
        return self.working_dir

    def to_dict(self) -> Dict[str, Any]:
        """Return configuration as dictionary"""
        return self._config.copy()

    def __repr__(self) -> str:
        return f"Config(codebase_path={self.codebase_path})"


def load_config(working_dir: Path = None) -> Config:
    """
    Load configuration from available sources

    Args:
        working_dir: Working directory for config file search

    Returns:
        Config object with merged settings
    """
    return Config(working_dir)
