"""Command-line interface: `bbs fetch`, `bbs validate`, `bbs inspect`, `bbs run`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bbs_pipeline.config import ProfileError, RunProfile, dump_profile, load_profile
from bbs_pipeline.contracts import PipelineExecutionResult
from bbs_pipeline.pipeline import run_profile
from bbs_pipeline.sources.release import fetch_release
from bbs_pipeline.stages.runs import MultipleRunsError
from bbs_pipeline.usage import MemoryEstimateError

STAGE_LABELS: tuple[tuple[str, str], ...] = (
    ("total_routes_raw", "routes in release"),
    ("routes_selected", "routes after route filters"),
    ("total_sampling_events_raw", "runs in release"),
    ("protocol_filtered_events", "runs after run filters"),
    ("runs_after_continuity", "runs after continuity"),
    ("continuity_filtered_routes", "routes with surviving runs"),
    ("target_species_count", "target species"),
    ("zero_filled_matrix_rows", "observation rows"),
    ("final_output_rows", "output rows"),
)


def _add_profile_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Run profile (TOML), or a <file>.manifest.json to rerun that build",
    )
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Override a profile value, e.g. --set runs.start_year=2000 (repeatable)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Never use the network (same as --set source.offline=true)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bbs",
        description="Config-driven extraction of North American Breeding Bird Survey data",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="Download a BBS release once and record release.json")
    fetch.add_argument(
        "--release",
        help="Release DOI or ScienceBase item id (default: the profile's, else the 2026 release)",
    )
    fetch.add_argument("--dest", type=Path, help="Directory for the release files")
    fetch.add_argument(
        "--config", type=Path, help="Take --release and --dest from this run profile's [source]"
    )

    values = sub.add_parser(
        "values", help="List valid values for a profile field, e.g. routes.states or joins.weather"
    )
    _add_profile_args(values)
    values.add_argument(
        "field",
        help="routes.states | routes.bcrs | routes.strata | routes.route_ids | species.aous | "
        "species.common_names | species.families | species.orders | joins.<table> | "
        "guilds | guilds.<trait>",
    )

    for name, help_text in (
        ("validate", "Check a run profile and print it with every default filled in"),
        ("inspect", "Dry run: counts after each stage and the exact output columns"),
        ("run", "Run the extraction and write the output file and manifest"),
    ):
        _add_profile_args(sub.add_parser(name, help=help_text))
    return parser


def _load(args: argparse.Namespace) -> RunProfile:
    overrides = list(args.overrides)
    if args.offline:
        overrides.append("source.offline=true")
    return load_profile(args.config, overrides)


def _fetch(args: argparse.Namespace) -> int:
    source = load_profile(args.config).source if args.config else None
    release = args.release or (source.release if source else RunProfile().source.release)
    dest = args.dest or (source.raw_dir if source else None)
    if dest is None:
        raise ProfileError("bbs fetch needs --dest or --config")
    info = fetch_release(release, dest)
    print(
        json.dumps(
            {
                "title": info.title,
                "doi": info.doi,
                "item_id": info.item_id,
                "dest": str(dest),
                "files": [f.name for f in info.files],
            },
            indent=2,
        )
    )
    return 0


def _print_stages(result: PipelineExecutionResult) -> None:
    metrics = result.funnel_metrics.model_dump()
    print(f"data source: {metrics['data_source']}")
    for key, label in STAGE_LABELS:
        print(f"  {label:<28} {metrics[key]:>12,}")
    print(f"  {'rows in output file':<28} {result.output_rows:>12,}")
    if result.guild_report is not None:
        _print_guilds(result.guild_report)
    print(f"output columns ({len(result.output_columns)}):")
    for col in result.output_columns:
        print(f"  {col}")


def _print_guilds(report: dict, limit: int = 25) -> None:
    unmatched, missing = report["unmatched_rows"], report["species_without_guild"]
    print(
        f"guild file: {report['file']} ({report['matched_rows']} of {report['rows']} rows "
        f"matched; traits: {', '.join(report['traits'])})"
    )
    if unmatched:
        print(f"  guild rows matching no BBS species ({len(unmatched)}):")
        for row in unmatched[:limit]:
            print(f"    {row}")
    if missing:
        print(f"  species recorded on kept runs with no guild row ({len(missing)}):")
        for sp in missing[:limit]:
            print(f"    {sp['AOU']} {sp.get('English_Common_Name') or ''}")
    if len(unmatched) > limit or len(missing) > limit:
        print(f"  (first {limit} shown; the manifest lists all)")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "fetch":
            return _fetch(args)
        profile = _load(args)
        if args.command == "values":
            from bbs_pipeline.values import collect_values  # noqa: PLC0415 (loads the cache)

            print("\n".join(collect_values(profile).field_values(args.field)))
            return 0
        if args.command == "validate":
            print(dump_profile(profile), end="")
            return 0
        result = run_profile(profile, export=args.command == "run")
        _print_stages(result)
        for path in result.exported_files:
            print(f"wrote {path}")
        return 0
    except (ProfileError, KeyError, MultipleRunsError, MemoryEstimateError) as exc:
        print(exc.args[0] if isinstance(exc, KeyError) else exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
