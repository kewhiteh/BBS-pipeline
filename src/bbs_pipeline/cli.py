"""Command-Line Interface for BBS-Pipeline v3."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bbs_pipeline.contracts import PipelineConfigBundle
from bbs_pipeline.pipeline import run_bbs_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bbs-pipeline",
        description="North American Breeding Bird Survey (BBS) v3 Spatial & Ecological ETL Pipeline",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path("config"),
        help="Directory containing guilds.json, protocol.json, and spatial.json (default: config)",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the three JSON configuration contracts and exit without running ETL",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    bundle = PipelineConfigBundle.load_from_dir(args.config_dir)
    if args.validate_only:
        summary = {
            "status": "valid",
            "config_dir": str(args.config_dir),
            "active_guild": bundle.guilds.active_guild,
            "guild_count": len(bundle.guilds.profiles),
            "ingress_mode": bundle.protocol.ingress_mode.value,
            "year_range": list(bundle.protocol.year_range),
            "target_crs_list": bundle.spatial.target_crs_list,
            "export_formats": [f.value for f in bundle.spatial.export_formats],
        }
        print(json.dumps(summary, indent=2))
        return 0

    result = run_bbs_pipeline(bundle)
    output = {
        "funnel_metrics": result.funnel_metrics.model_dump(mode="json"),
        "active_joins": result.active_joins,
        "exported_files": [str(p) for p in result.exported_files],
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
