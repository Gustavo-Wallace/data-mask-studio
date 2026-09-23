from contextlib import contextmanager

import pytest

from data_mask_studio import environment
from data_mask_studio.anonymization import ColumnAction as Action, ColumnConfig
from data_mask_studio.batch import BatchFile, BatchService
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.csv_tools.source_binding import source_ref_at
from data_mask_studio.gui.anonymization_worker import AnonymizationWorker
from data_mask_studio.processing import CompositeColumnConfig, CompositeSource, build_processing_plan
from data_mask_studio.profiles import ProfileRepository, ProfileService


@pytest.mark.parametrize('caller', ['gui', 'batch'])
@pytest.mark.parametrize('kind', ['preserve', 'mask', 'mixed', 'composite_preserve', 'composite_mask'])
def test_requirement_controls_busy_environment(tmp_path, monkeypatch, caller, kind):
    source = tmp_path / 'source.csv'
    source.write_text('A,B\nfirst,second\n', encoding='utf-8')
    inspection = inspect_csv(source)
    configs = [ColumnConfig('A'), ColumnConfig('B')]
    composites = []
    if kind in ('mask', 'mixed'):
        configs[0] = ColumnConfig('A', True, 'AA')
        if kind == 'mask':
            configs[1] = ColumnConfig('B', True, 'BB')
    if kind.startswith('composite'):
        action = Action.MASK if kind == 'composite_mask' else Action.PRESERVE
        configs = [ColumnConfig(h, action=Action.EXCLUDE) for h in inspection.headers]
        composites = [CompositeColumnConfig('AB', 'AB' if action is Action.MASK else '',
            tuple(CompositeSource(source_ref_at(inspection, i)) for i in range(2)), action=action)]
    plan = build_processing_plan(inspection, configs, composites)
    accesses = []
    class Provider:
        @property
        def key_path(self):
            accesses.append('directory')
            return tmp_path / 'environment' / 'secret.key'
        def get_key(self):
            pytest.fail('No key access before a busy lease, or for preserve-only')
    def vault():
        pytest.fail('No vault initialization before a busy lease, or for preserve-only')
    service = ProfileService(ProfileRepository(tmp_path / 'profiles.json'))
    profile = service.create('Plan requirements', configs, inspection=inspection, composites=composites)
    batch = BatchService(service)
    items = [BatchFile(source)]
    batch.validate(items, profile)
    @contextmanager
    def busy(*args, **kwargs):
        assert not kwargs.get('exclusive', False)
        accesses.append('lease')
        raise environment.EnvironmentError('Environment busy')
        yield
    monkeypatch.setattr(environment, 'environment_lease', busy)
    if caller == 'gui':
        worker = AnonymizationWorker(inspection, str(tmp_path / 'output.csv'), configs,
                                     Provider(), vault, overwrite=False, processing_plan=plan)
        completed, failed = [], []
        worker.completed.connect(completed.append)
        worker.failed.connect(failed.append)
        worker.run()
        assert bool(failed) is plan.requires_masking
        assert bool(completed) is not plan.requires_masking
    else:
        output = tmp_path / 'out'
        output.mkdir()
        if plan.requires_masking:
            with pytest.raises(environment.EnvironmentError, match='busy'):
                batch.process(items, profile, output, Provider(), vault)
        else:
            assert batch.process(items, profile, output, Provider(), vault).completed_files == 1
    assert accesses == (['directory', 'lease'] if plan.requires_masking else [])


def test_legacy_gui_worker_keeps_lease(tmp_path, monkeypatch):
    source = tmp_path / 'source.csv'
    source.write_text('A\nvalue\n', encoding='utf-8')
    class Provider:
        key_path = tmp_path / 'secret.key'
        def get_key(self):
            pytest.fail('Busy environment must precede key access')
    @contextmanager
    def busy(*args, **kwargs):
        assert not kwargs.get('exclusive', False)
        raise environment.EnvironmentError('Environment busy')
        yield
    monkeypatch.setattr(environment, 'environment_lease', busy)
    worker = AnonymizationWorker(inspect_csv(source), str(tmp_path / 'output.csv'),
                                 [ColumnConfig('A', True, 'AA')], Provider(), overwrite=False)
    failed = []
    worker.failed.connect(failed.append)
    worker.run()
    assert len(failed) == 1 and isinstance(failed[0], environment.EnvironmentError)
    assert not (tmp_path / 'output.csv').exists()


def test_batch_rebinds_before_consulting_environment(tmp_path):
    source = tmp_path / 'source.csv'
    source.write_text('A\nvalue\n', encoding='utf-8')
    service = ProfileService(ProfileRepository(tmp_path / 'profiles.json'))
    profile = service.create('Mask plan', [ColumnConfig('A', True, 'AA')], inspection=inspect_csv(source))
    batch = BatchService(service)
    items = [BatchFile(source)]
    batch.validate(items, profile)
    source.write_text('UNKNOWN\nvalue\n', encoding='utf-8')
    class ForbiddenProvider:
        @property
        def key_path(self):
            pytest.fail('Invalid binding must be detected before environment lookup')
    summary = batch.process(items, profile, tmp_path, ForbiddenProvider())
    assert summary.incompatible_files == 1
    assert summary.completed_files == 0
