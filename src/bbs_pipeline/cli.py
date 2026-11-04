"""Command-Line Interface for BBS-Pipeline v3."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bbs_pipeline.contracts import PipelineConfigBundle, ProtocolConfigContract
from bbs_pipeline.pipeline import run_bbs_pipeline
from bbs_pipeline.sources.release import fetch_release


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bbs",
        description="North American Breeding Bird Survey (BBS) v3 Spatial & Ecological ETL Pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser(
        "fetch",
        help="Download a BBS release from ScienceBase once and record it in release.json",
    )
    fetch.add_argument(
        "--release",
        default=ProtocolConfigContract().sciencebase_doi,
        help="Release DOI (e.g. 10.5066/P144YU3S) or ScienceBase item id (default: %(default)s)",
    )
    fetch.add_argument(
        "--dest",
        type=Path,
        required=True,
        help="Directory for the release files; existing files with matching MD5 are kept",
    )

    run = sub.add_parser("run", help="Run the pipeline from the JSON configuration contracts")
    run.add_argument(
        "--config-dir",
        type=Path,
        default=Path("config"),
        help="Directory containing guilds.json, protocol.json, and spatial.json (default: config)",
    )
    run.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the three JSON configuration contracts and exit without running ETL",
    )
    run.add_argument(
        "--offline",
        action="store_true",
        help="Never use the network; fail if the release has not been fetched",
    )
    return parser


def _fetch(args: argparse.Namespace) -> int:
    info = fetch_release(args.release, args.dest)
    print(
        json.dumps(
            {
                "title": info.title,
                "doi": info.doi,
                "item_id": info.item_id,
                "dest": str(args.dest),
                "files": [f.name for f in info.files],
            },
            indent=2,
        )
    )
    return 0


def _run(args: argparse.Namespace) -> int:
    bundle = PipelineConfigBundle.load_from_dir(args.config_dir)
    if args.offline:
        bundle = bundle.model_copy(
            update={"protocol": bundle.protocol.model_copy(update={"offline": True})}
        )
    if args.validate_only:
        summary = {
            "status": "valid",
            "config_dir": str(args.config_dir),
            "active_guild": bundle.guilds.active_guild,
            "guild_count": len(bundle.guilds.profiles),
            "release": bundle.protocol.sciencebase_doi,
            "offline": bundle.protocol.offline,
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "fetch":
        return _fetch(args)
    return _run(args)


if __name__ == "__main__":
    sys.exit(main())
