"""Contratos preparatórios de planejamento; sem execução de linhas."""

from .models import CompositeColumnConfig, CompositeSource, ProcessingPlan
from .planner import PlanningError, build_processing_plan

__all__ = [
    "CompositeColumnConfig", "CompositeSource", "ProcessingPlan",
    "PlanningError", "build_processing_plan",
]
