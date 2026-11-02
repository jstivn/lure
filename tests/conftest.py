"""Shared helpers: both control-hub and meta-lead-gen have a package named `core`, so meta modules are loaded by path."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
META_DIR = ROOT / "meta-lead-gen"
HUB_DIR = ROOT / "control-hub"

sys.path.insert(0, str(HUB_DIR))


def load_meta(name: str):
    spec = importlib.util.spec_from_file_location(f"meta_{name}", META_DIR / "core" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
