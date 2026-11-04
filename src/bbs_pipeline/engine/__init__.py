"""Pipeline engine: runs the stages in ``bbs_pipeline.stages`` in order."""

from bbs_pipeline.engine.ecology import EcologicalEngineResult, run_ecological_engine

__all__ = ["EcologicalEngineResult", "run_ecological_engine"]
