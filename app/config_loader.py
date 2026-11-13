from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional
import yaml

from app.schemas.workflow import WorkflowConfig


class WorkflowConfigError(Exception):
    pass


class WorkflowRegistry:
    """Loads, validates, and caches declarative workflow definitions from YAML files."""

    def __init__(self, config_dir: Path | str = "configs/workflows") -> None:
        self.config_dir = Path(config_dir)
        self._workflows: Dict[str, WorkflowConfig] = {}
        self._file_map: Dict[str, Path] = {}
        self._mtimes: Dict[Path, float] = {}
        self.reload()

    def reload(self) -> None:
        if not self.config_dir.exists():
            raise WorkflowConfigError(f"Config directory not found: {self.config_dir}")

        loaded: Dict[str, WorkflowConfig] = {}
        file_map: Dict[str, Path] = {}
        mtimes: Dict[Path, float] = {}

        for path in sorted(self.config_dir.glob("*.y*ml")):
            raw_text = path.read_text(encoding="utf-8")
            raw_data = yaml.safe_load(raw_text)
            if not raw_data:
                continue
            cfg = WorkflowConfig.model_validate(raw_data)
            canonical = json.dumps(raw_data, sort_keys=True)
            cfg.config_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
            loaded[cfg.workflow_id] = cfg
            file_map[cfg.workflow_id] = path
            mtimes[path] = path.stat().st_mtime

        self._workflows = loaded
        self._file_map = file_map
        self._mtimes = mtimes

    def _refresh_if_changed(self) -> None:
        current_files = sorted(self.config_dir.glob("*.y*ml"))
        if len(current_files) != len(self._mtimes):
            self.reload()
            return
        for path in current_files:
            if path not in self._mtimes or path.stat().st_mtime != self._mtimes[path]:
                self.reload()
                return

    def get(self, workflow_id: str) -> Optional[WorkflowConfig]:
        self._refresh_if_changed()
        return self._workflows.get(workflow_id)

    def list_workflows(self) -> List[WorkflowConfig]:
        self._refresh_if_changed()
        return list(self._workflows.values())

    def get_raw_yaml(self, workflow_id: str) -> Optional[str]:
        self._refresh_if_changed()
        path = self._file_map.get(workflow_id)
        if not path or not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def update_raw_yaml(self, workflow_id: str, yaml_content: str) -> WorkflowConfig:
        try:
            parsed = yaml.safe_load(yaml_content)
            if not isinstance(parsed, dict):
                raise WorkflowConfigError("YAML root must be a key-value mapping.")
            validated = WorkflowConfig.model_validate(parsed)
        except Exception as exc:
            raise WorkflowConfigError(f"Invalid workflow YAML: {exc}") from exc

        if validated.workflow_id != workflow_id:
            raise WorkflowConfigError(
                f"YAML workflow_id '{validated.workflow_id}' does not match target '{workflow_id}'."
            )

        target_path = self._file_map.get(workflow_id) or (self.config_dir / f"{workflow_id}.yaml")
        target_path.write_text(yaml_content, encoding="utf-8")
        self.reload()
        updated = self._workflows.get(workflow_id)
        if updated is None:
            raise WorkflowConfigError(f"Failed to reload updated workflow '{workflow_id}'.")
        return updated
