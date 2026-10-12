"""分析档案与初筛档案的文件读取。"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

_MCP = Path(__file__).resolve().parents[2]


def _path(env_key: str, default: Path) -> Path:
    return Path(os.environ.get(env_key, default))


def analysis_profile_path() -> Path:
    return _path("ANALYSIS_PROFILE", _MCP.parent / "research-config" / "analysis-profile.yaml")


def screening_profile_path() -> Path:
    return _path("SCREENING_PROFILE", _MCP.parent / "research-config" / "screening-profile.yaml")


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_analysis_profile() -> dict:
    return load_yaml(analysis_profile_path())


def load_screening_profile() -> dict:
    return load_yaml(screening_profile_path())
