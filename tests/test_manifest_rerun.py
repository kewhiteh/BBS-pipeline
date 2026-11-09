"""H12: a run's manifest reopens as its exact profile (CLI and editor)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bbs_pipeline.cli import main as cli_main
from bbs_pipeline.config import ProfileError, load_profile, profile_from_data
from bbs_pipeline.pipeline import run_profile

RELEASE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "real" / "release"


def _profile(tmp_path: Path, out: str = "a.csv"):
    return profile_from_data(
        {
            "source": {
                "release": "10.5066/P144YU3S",
                "raw_dir": str(RELEASE),
                "cache_dir": str(tmp_path / "cache"),
                "offline": True,
            },
            "runs": {"start_year": 2015, "end_year": 2024},
            "observations": {"source": "50_stop"},
            "species": {"aous": ["07550"]},
            "output": {"path": str(tmp_path / out)},
        }
    )


def test_manifest_loads_as_its_profile_and_reruns_identically(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    run_profile(profile)
    manifest = tmp_path / "a.csv.manifest.json"

    reopened = load_profile(manifest)
    assert reopened == profile

    again = load_profile(manifest, [f'output.path="{tmp_path / "b.csv"}"'])
    run_profile(again)
    assert (tmp_path / "b.csv").read_bytes() == (tmp_path / "a.csv").read_bytes()


def test_cli_runs_from_a_manifest(tmp_path: Path) -> None:
    run_profile(_profile(tmp_path))
    manifest = str(tmp_path / "a.csv.manifest.json")
    out = tmp_path / "c.csv"
    assert cli_main(["run", "--config", manifest, "--set", f'output.path="{out}"']) == 0
    assert out.read_bytes() == (tmp_path / "a.csv").read_bytes()


def test_bad_manifests_say_why(tmp_path: Path) -> None:
    broken = tmp_path / "x.manifest.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileError, match="not valid JSON"):
        load_profile(broken)
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"hello": 1}), encoding="utf-8")
    with pytest.raises(ProfileError, match="no recorded profile"):
        load_profile(other)
