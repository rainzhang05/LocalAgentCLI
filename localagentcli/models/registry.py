"""ModelRegistry — JSON-based registry of installed local models."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from filelock import FileLock


@dataclass
class ModelEntry:
    """A registered local model stored in registry.json."""

    name: str
    version: str  # "v1", "v2", etc.
    format: str  # "gguf" | "mlx" | "safetensors"
    path: str  # absolute path to version directory
    size_bytes: int = 0
    capabilities: dict = field(
        default_factory=lambda: {
            "tool_use": False,
            "reasoning": False,
            "streaming": True,
        }
    )
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "name": self.name,
            "version": self.version,
            "format": self.format,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "capabilities": self.capabilities,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ModelEntry:
        """Deserialize from a dict."""
        return cls(
            name=data.get("name", ""),
            version=data.get("version", "v1"),
            format=data.get("format", ""),
            path=data.get("path", ""),
            size_bytes=data.get("size_bytes", 0),
            capabilities=data.get(
                "capabilities",
                {
                    "tool_use": False,
                    "reasoning": False,
                    "streaming": True,
                },
            ),
            metadata=data.get("metadata", {}),
        )


class ModelRegistry:
    """Manages installed local models, stored in registry.json."""

    def __init__(self, registry_path: Path):
        self._path = registry_path
        self._lock = FileLock(str(registry_path) + ".lock")

    def list_models(self) -> list[ModelEntry]:
        """Return all registered models."""
        entries = self._load()
        return [ModelEntry.from_dict(e) for e in entries]

    def get_model(self, name: str, version: str | None = None) -> ModelEntry | None:
        """Get a model by name. If version is None, return the latest version."""
        entries = self._load()
        matches = [e for e in entries if e["name"] == name]
        if not matches:
            return None
        if version:
            for e in matches:
                if e["version"] == version:
                    return ModelEntry.from_dict(e)
            return None
        # Return latest version (highest vN number)
        matches.sort(key=lambda e: _version_number(e["version"]))
        return ModelEntry.from_dict(matches[-1])

    def register(self, entry: ModelEntry) -> None:
        """Add a new model to the registry."""
        entries = self._load()
        # Check for duplicate name+version
        for e in entries:
            if e["name"] == entry.name and e["version"] == entry.version:
                raise ValueError(
                    f"Model '{entry.name}' version '{entry.version}' already registered"
                )
        entries.append(entry.to_dict())
        self._save(entries)

    def unregister(self, name: str, version: str | None = None) -> None:
        """Remove a model from the registry.

        If version is None, remove all versions.
        """
        entries = self._load()
        if version:
            before = len(entries)
            entries = [e for e in entries if not (e["name"] == name and e["version"] == version)]
            if len(entries) == before:
                raise KeyError(f"Model '{name}' version '{version}' not found")
        else:
            before = len(entries)
            entries = [e for e in entries if e["name"] != name]
            if len(entries) == before:
                raise KeyError(f"Model '{name}' not found")
        self._save(entries)

    def update(self, name: str, updates: dict) -> None:
        """Update fields of the latest version of a model."""
        entries = self._load()
        matches = [(i, e) for i, e in enumerate(entries) if e["name"] == name]
        if not matches:
            raise KeyError(f"Model '{name}' not found")
        # Update the latest version
        matches.sort(key=lambda pair: _version_number(pair[1]["version"]))
        idx = matches[-1][0]
        for key, value in updates.items():
            entries[idx][key] = value
        self._save(entries)

    def search(self, query: str) -> list[ModelEntry]:
        """Search installed models by name, format, or metadata."""
        entries = self._load()
        q = query.lower()
        results = []
        for e in entries:
            if q in e.get("name", "").lower():
                results.append(ModelEntry.from_dict(e))
                continue
            if q in e.get("format", "").lower():
                results.append(ModelEntry.from_dict(e))
                continue
            meta = e.get("metadata", {})
            for val in meta.values():
                if isinstance(val, str) and q in val.lower():
                    results.append(ModelEntry.from_dict(e))
                    break
        return results

    def next_version(self, name: str) -> str:
        """Compute the next version string for a model name."""
        entries = self._load()
        matches = [e for e in entries if e["name"] == name]
        if not matches:
            return "v1"
        max_v = max(_version_number(e["version"]) for e in matches)
        return f"v{max_v + 1}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> list[dict]:
        """Load entries from registry.json."""
        with self._lock:
            if not self._path.exists():
                return []
            try:
                text = self._path.read_text(encoding="utf-8")
                data = json.loads(text)
                if isinstance(data, list):
                    return data
                return []
            except (json.JSONDecodeError, OSError):
                return []

    def _save(self, entries: list[dict]) -> None:
        """Write entries to registry.json."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(entries, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )


def _version_number(version: str) -> int:
    """Extract the numeric part from a version string like 'v1'."""
    try:
        return int(version.lstrip("v"))
    except (ValueError, AttributeError):
        return 0
