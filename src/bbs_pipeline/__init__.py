"""USGS Breeding Bird Survey (BBS) Pipeline."""

import importlib.metadata

try:
    __version__ = importlib.metadata.version("bbs_pipeline")
except importlib.metadata.PackageNotFoundError:
    __version__ = "2.0.0.dev0"
