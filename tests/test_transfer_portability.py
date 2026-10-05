"""Selective transfer across independent Windows environments, using real DPAPI."""

import csv
import hashlib
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnAction, ColumnConfig
from data_mask_studio.app import create_application
from data_mask_studio.backup.paths import default_environment_paths
from data_mask_studio.composite_text import composite_text
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.csv_tools.csv_anonymizer import anonymize_csv
from data_mask_studio.csv_tools.source_binding import source_ref_at
from data_mask_studio.environment import GENERATION_FILE, generation, environment_lease
from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.normalization import NormalizationRule as Rule
from data_mask_studio.processing import CompositeColumnConfig, CompositeSource, build_processing_plan
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.restoration import (
    MissingCodeError, MissingCodePolicy, RepresentationPolicy, RestorationConfiguration,
    RestorationSecurityError, SelectedColumn, restore_csv, restore_csv_from_package,
)
from data_mask_studio.security import LocalKeyProvider, WindowsDPAPIProtector
from data_mask_studio.transfer_package import encrypt_package, read_package, write_package
from data_mask_studio.transfer_package.staging import PackageStagingRequest
from data_mask_studio.vault import MappingCandidate
from data_mask_studio.vault.defaults import create_default_vault_repository
from data_mask_studio.vault.key_provider import VaultKeyProvider

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Real DPAPI and environment leases require Windows")
PASSWORD = "synthetic portability package password"
ROWS = [
    ["999.999.999-99", "  Synthetic Person  ", "preserved-A"],
    ["888.888.888-88", "Other Synthetic Person", "preserved-B"],
    ["999.999.999-99", "  Synthetic Person  ", "preserved-C"],
]


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.reader(stream))


def environment_fingerprint(paths):
    # Logical SQLite snapshot includes WAL, avoids file-copy snapshot assumptions.
    with closing(sqlite3.connect(paths.vault_database_path.as_uri() + "?mode=ro", uri=True)) as db:
        database = hashlib.sha256("\n".join(db.iterdump()).encode()).digest()
    files = tuple((path.name, path.read_bytes()) for path in (
        paths.hmac_key_path, paths.vault_key_path, paths.profiles_path))
    return database, files, generation(paths.directory), (paths.directory / GENERATION_FILE).exists()


def assert_absent(repo, scalars, composites):
    assert all(repo.get_mapping_for_inspection(code) is None for code in scalars)
    assert all(repo.composite_repository().get_composite_mapping(code) is None for code in composites)
    assert all(repo.get_mapping_for_inspection(code) is None for code in composites)


@pytest.fixture
def environments(tmp_path, monkeypatch):
    appdata_a, appdata_b = tmp_path / "appdata-A", tmp_path / "appdata-B"
    transfer = tmp_path / "transfer"
    transfer.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(appdata_a))
    paths_a = default_environment_paths()
    repo_a = create_default_vault_repository()
    source = transfer / "source.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows([["DOCUMENTO", "NOME", "ETIQUETA"], *ROWS])
    inspection = inspect_csv(source)
    columns = [ColumnConfig("DOCUMENTO", True, "DOC", Rule.CPF),
               ColumnConfig("NOME", action=ColumnAction.EXCLUDE), ColumnConfig("ETIQUETA")]
    composite = CompositeColumnConfig("PESSOA", "PERSON", (
        CompositeSource(source_ref_at(inspection, 1), Rule.PERSON_NAME),
        CompositeSource(source_ref_at(inspection, 0), Rule.CPF),
    ), action=ColumnAction.MASK)
    ProfileService(ProfileRepository()).create("Synthetic A", columns,
        composites=[composite], inspection=inspection)
    plan = build_processing_plan(inspection, columns, [composite])
    masked, package = transfer / "masked.csv", transfer / "portable.dmspackage"
    result = anonymize_csv(source, masked, encoding=inspection.encoding, delimiter=inspection.delimiter,
        secret_key=LocalKeyProvider().load_existing_key(), processing_plan=plan, vault_repository=repo_a,
        transfer_package_request=PackageStagingRequest(PASSWORD), transfer_package_destination=package)
    masked_inspection = inspect_csv(masked)
    config = RestorationConfiguration(masked, masked_inspection.encoding, masked_inspection.delimiter,
        tuple(masked_inspection.headers), (SelectedColumn(0, "DOCUMENTO"), SelectedColumn(2, "PESSOA")),
        MissingCodePolicy.ABORT, RepresentationPolicy.FIRST_ORIGINAL)
    expected = transfer / "expected.csv"
    restore_csv(config, expected, repo_a.as_read_only())
    assert read_rows(expected) == [["DOCUMENTO", "ETIQUETA", "PESSOA"], *[
        [doc, label, composite_text((name, doc))] for doc, name, label in ROWS]]

    monkeypatch.setenv("LOCALAPPDATA", str(appdata_b))
    paths_b = default_environment_paths()
    repo_b = create_default_vault_repository()
    ProfileRepository().save([])
    assert paths_a.directory == appdata_a / "DataMaskStudio"
    assert paths_b.directory == appdata_b / "DataMaskStudio"
    assert paths_a.directory != paths_b.directory
    protector = WindowsDPAPIProtector()
    keys = []
    for paths in (paths_a, paths_b):
        hmac = LocalKeyProvider(paths.directory).load_existing_key()
        aes = VaultKeyProvider(paths.directory).load_existing_key()
        assert hmac != aes
        assert protector.unprotect(paths.hmac_key_path.read_bytes()) == hmac
        assert protector.unprotect(paths.vault_key_path.read_bytes()) == aes
        assert hmac not in paths.hmac_key_path.read_bytes()
        assert aes not in paths.vault_key_path.read_bytes()
        keys.append((hashlib.sha256(hmac).digest(), hashlib.sha256(aes).digest()))
    assert keys[0][0] != keys[1][0] and keys[0][1] != keys[1][1]
    # New environments legitimately have no generation marker; do not fabricate one.
    assert generation(paths_a.directory) == generation(paths_b.directory) == b""
    assert not (paths_a.directory / GENERATION_FILE).exists()
    assert not (paths_b.directory / GENERATION_FILE).exists()
    with environment_lease(paths_a.directory, exclusive=True):
        with environment_lease(paths_b.directory, exclusive=True):
            pass
    assert ProfileService(ProfileRepository()).list_profiles() == []
    assert repo_b.count() == 0
    assert len(result.emitted_scalar_codes) == len(result.emitted_composite_codes) == 2
    assert_absent(repo_b, result.emitted_scalar_codes, result.emitted_composite_codes)
    return paths_a, paths_b, repo_a, repo_b, config, package, expected, result


def test_package_restores_in_independent_process_without_import(environments, tmp_path):
    paths_a, paths_b, _, repo_b, config, package, expected, emitted = environments
    before_a, before_b = environment_fingerprint(paths_a), environment_fingerprint(paths_b)
    with pytest.raises(MissingCodeError):
        restore_csv(config, tmp_path / "local-cannot-restore.csv", repo_b.as_read_only())
    assert not (tmp_path / "local-cannot-restore.csv").exists()
    output = tmp_path / "restored-in-B.csv"
    program = '''
import sys
from pathlib import Path
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.restoration import RestorationConfiguration, SelectedColumn, MissingCodePolicy, RepresentationPolicy, restore_csv_from_package
from data_mask_studio.vault.defaults import create_default_vault_repository
repo = create_default_vault_repository()
assert repo.count() == 0
source, package, output = map(Path, sys.argv[1:4])
inspection = inspect_csv(source)
config = RestorationConfiguration(source, inspection.encoding, inspection.delimiter, tuple(inspection.headers),
    (SelectedColumn(0, 'DOCUMENTO'), SelectedColumn(2, 'PESSOA')), MissingCodePolicy.ABORT, RepresentationPolicy.FIRST_ORIGINAL)
result = restore_csv_from_package(config, output, package, sys.argv[4])
assert result.restored_codes == 6
assert repo.count() == 0
'''
    # No key, vault path or originating environment passed to the new interpreter.
    completed = subprocess.run([sys.executable, "-c", program, str(config.source_path), str(package),
        str(output), PASSWORD], env={**os.environ, "LOCALAPPDATA": str(paths_b.directory.parent)},
        capture_output=True, timeout=30)
    assert completed.returncode == 0, "Isolated package restoration process failed"
    assert output.read_bytes() == expected.read_bytes()
    assert environment_fingerprint(paths_a) == before_a
    assert environment_fingerprint(paths_b) == before_b
    assert_absent(repo_b, emitted.emitted_scalar_codes, emitted.emitted_composite_codes)


def test_default_application_in_B_restores_real_package(environments, tmp_path):
    _, paths_b, _, repo_b, config, package, expected, emitted = environments
    before = environment_fingerprint(paths_b)
    app = create_application([])
    window = MainWindow()  # Default bootstrap, recovery, profiles and real local key providers.
    assert window._backup_paths.directory == paths_b.directory
    widget = window.restoration_widget
    widget.load_csv(str(config.source_path))
    widget._checkboxes[0].setChecked(True)
    widget._checkboxes[2].setChecked(True)
    widget.source_combo.setCurrentIndex(1)
    widget.package_controls.path.setText(str(package))
    widget.package_controls.password.setText(PASSWORD)
    output = tmp_path / "gui-restored-in-B.csv"
    widget.start_restoration(output, overwrite=False)
    worker = widget._worker
    assert worker is not None and worker.wait(15000)
    assert worker._package_request is None
    app.processEvents()
    assert widget._worker is None and "sucesso" in widget.status_label.text()
    assert output.read_bytes() == expected.read_bytes()
    assert not widget.package_controls.password.text()
    assert environment_fingerprint(paths_b) == before
    assert_absent(repo_b, emitted.emitted_scalar_codes, emitted.emitted_composite_codes)


@pytest.mark.parametrize("local_state", ["empty", "unrelated", "conflicting"])
@pytest.mark.parametrize("case", ["success", "wrong_password", "tampered_csv", "different_csv", "different_package", "missing_scalar", "missing_composite"])
def test_B_local_state_cannot_override_package(environments, tmp_path, local_state, case):
    paths_a, paths_b, _, repo_b, config, package, expected, emitted = environments
    if local_state != "empty":
        with repo_b.transaction() as tx:
            codes = emitted.emitted_scalar_codes if local_state == "conflicting" else ("OTHER-ABCDEFGHIJKL",)
            tx.upsert_batch([MappingCandidate(code, "DOC" if local_state == "conflicting" else "OTHER",
                "synthetic local override", "DOCUMENTO") for code in codes])
    # Composites must remain absent, even if scalar codes have local conflicts.
    assert all(repo_b.composite_repository().get_composite_mapping(code) is None
               for code in emitted.emitted_composite_codes)
    before_a, before_b = environment_fingerprint(paths_a), environment_fingerprint(paths_b)
    password = PASSWORD
    if case == "wrong_password": password = "incorrect synthetic password"
    elif case == "tampered_csv": config.source_path.write_bytes(config.source_path.read_bytes() + b"\n")
    elif case == "different_csv":
        other = tmp_path / "other.csv"
        other.write_text("DOCUMENTO,ETIQUETA,PESSOA\nplain,other,plain\n", encoding="utf-8")
        config = replace(config, source_path=other)
    elif case == "different_package":
        payload = read_package(package, PASSWORD)
        # A valid, authenticated package bound to another file, not random corruption.
        from data_mask_studio.transfer_package.binding import compute_file_binding
        other = tmp_path / "other-masked.csv"
        other.write_bytes(b"DOCUMENTO,ETIQUETA,PESSOA\nother,data,other\n")
        package = write_package(tmp_path / "different.dmspackage",
            replace(payload, masked_file=compute_file_binding(other)), PASSWORD)
    elif case.startswith("missing_"):
        kind = case.removeprefix("missing_")
        payload = read_package(package, PASSWORD)
        package.write_bytes(encrypt_package(replace(payload, **{kind + "_mappings": ()}), PASSWORD))
    output = tmp_path / "portable-output.csv"
    if case == "success":
        result = restore_csv_from_package(config, output, package, password)
        assert result.restored_codes == 6
        assert output.read_bytes() == expected.read_bytes()
        assert b"synthetic local override" not in output.read_bytes()
    else:
        error = MissingCodeError if case.startswith("missing_") else RestorationSecurityError
        with pytest.raises(error):
            restore_csv_from_package(config, output, package, password)
        assert not output.exists()
    assert not list(tmp_path.glob("*.tmp"))
    assert environment_fingerprint(paths_a) == before_a
    assert environment_fingerprint(paths_b) == before_b
    if local_state == "empty":
        assert_absent(repo_b, emitted.emitted_scalar_codes, emitted.emitted_composite_codes)
