"""Template loader (Phase 2) — loads ``tpl_*.png`` from the assets dir.

Reads ``crops_manifest.json`` (when present) to recover each template's source
box, which doubles as its default search region. Templates are cached after
first load.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ckrbot.vision.vision import Region

DEFAULT_MANIFEST = "crops_manifest.json"


@dataclass(frozen=True)
class Template:
    """A loaded template image plus its default search region (if known)."""

    name: str
    image: np.ndarray  # BGR uint8
    region: Region | None  # source box from manifest, used as default ROI


class TemplateStore:
    """Loads and caches templates from an assets directory."""

    def __init__(self, assets_dir: str | Path, manifest_name: str = DEFAULT_MANIFEST) -> None:
        self._assets_dir = Path(assets_dir)
        self._cache: dict[str, Template] = {}
        self._manifest: dict[str, dict] = {}
        manifest_path = self._assets_dir / manifest_name
        if manifest_path.exists():
            with manifest_path.open("r", encoding="utf-8") as fh:
                self._manifest = json.load(fh)

    def region_of(self, name: str) -> Region | None:
        """Default search region for ``name`` from the manifest box, if any."""
        entry = self._manifest.get(name)
        if entry is None or "box" not in entry:
            return None
        x1, y1, x2, y2 = entry["box"]
        return (int(x1), int(y1), int(x2), int(y2))

    def load(self, name: str) -> Template:
        """Load template ``name`` (without the ``.png`` suffix), cached."""
        cached = self._cache.get(name)
        if cached is not None:
            return cached

        path = self._assets_dir / f"{name}.png"
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)  # BGR, alpha dropped
        if image is None:
            raise FileNotFoundError(f"template not found or unreadable: {path}")

        template = Template(name=name, image=image, region=self.region_of(name))
        self._cache[name] = template
        return template
