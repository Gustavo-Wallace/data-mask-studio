import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('first', [
    'data_mask_studio.processing',
    'data_mask_studio.processing.composite_identity',
    'data_mask_studio.csv_tools.source_binding',
])
def test_public_processing_imports_in_fresh_interpreter(tmp_path, first):
    environment = os.environ.copy()
    environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
    environment['LOCALAPPDATA'] = str(tmp_path / 'local')
    code = f'''
import importlib
import sys
importlib.import_module({first!r})
assert 'data_mask_studio.csv_tools.csv_anonymizer' not in sys.modules
from data_mask_studio.processing import (
    CompositeColumnConfig, CompositeSource, ProcessingPlan,
    PlanningError, build_processing_plan,
)
from data_mask_studio.processing import models, planner
assert CompositeColumnConfig is models.CompositeColumnConfig
assert CompositeSource is models.CompositeSource
assert ProcessingPlan is models.ProcessingPlan
assert PlanningError is planner.PlanningError
assert build_processing_plan is planner.build_processing_plan
from data_mask_studio.csv_tools import *
from data_mask_studio.csv_tools import csv_anonymizer
assert anonymize_csv is csv_anonymizer.anonymize_csv
assert CSVAnonymizationError is csv_anonymizer.CSVAnonymizationError
assert ProcessingCancelled is csv_anonymizer.ProcessingCancelled
import data_mask_studio.csv_tools as csv_tools
assert all(getattr(csv_tools, name) is not None for name in csv_tools.__all__)
try:
    csv_tools.unknown_public_symbol
except AttributeError:
    pass
else:
    raise AssertionError('Unknown exports must raise AttributeError')
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path,
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
