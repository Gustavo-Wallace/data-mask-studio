import os
from pathlib import Path
import tempfile
import time

import pytest

from data_mask_studio.maintenance.models import TemporaryItem
from data_mask_studio.maintenance.temporary_cleanup import cleanup_temporaries, locate_temporaries


def old(path):
    stamp = time.time() - 7200
    os.utime(path, (stamp, stamp))
    return path


@pytest.mark.parametrize("name", [
    "other-app.tmp", "report.tmp", "download.tmp", ".dms-write-", ".dms-write-short",
    ".dms-write-ab12_cd3.extra", "dms-write-ab12_cd3", ".dms-backup-ab12_cd3.tmp",
    ".report.csv.ab12_cd3.tmp", ".page.html.ab12_cd3.tmp",
])
def test_foreign_or_ambiguous_old_file_never_offered_or_removed(tmp_path, name):
    path = tmp_path / name
    path.write_text("third party", encoding="utf-8")
    old(path)
    assert locate_temporaries(tmp_path) == []
    forged = TemporaryItem(path, path.stat().st_size, 7200, False, False, selected=True)
    assert cleanup_temporaries([forged], tmp_path).preserved == 1
    assert path.read_text(encoding="utf-8") == "third party"


@pytest.mark.parametrize("prefix,suffix", [
    (".dms-write-", ""), (".dms-access-", ""), (".secret-", ".tmp"),
    (".profiles.json.", ".tmp"), (".saved.dmsbackup.", ".tmp"),
])
def test_actual_tempfile_names_are_recognized_and_removed(tmp_path, prefix, suffix):
    fd, name = tempfile.mkstemp(dir=tmp_path, prefix=prefix, suffix=suffix)
    os.close(fd)
    path = old(Path(name))
    foreign = tmp_path / "other-app.tmp"
    foreign.write_text("keep", encoding="utf-8")
    old(foreign)
    items = locate_temporaries(tmp_path)
    assert [item.path for item in items] == [path]
    items[0].selected = True
    assert cleanup_temporaries(items, tmp_path).removed == 1
    assert not path.exists() and foreign.exists()


def test_cleanup_rechecks_age_and_namespace(tmp_path):
    path = tmp_path / ".dms-write-ab12_cd3"
    path.touch()
    old(path)
    item = locate_temporaries(tmp_path)[0]
    item.selected = True
    path.touch()
    assert cleanup_temporaries([item], tmp_path).preserved == 1
    assert path.exists()
    renamed = path.rename(tmp_path / "report.tmp")
    item.path = renamed
    item.recent = False
    old(renamed)
    assert cleanup_temporaries([item], tmp_path).preserved == 1
    assert renamed.exists()


def test_local_only_patterns_not_owned_in_output_directory(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    path = output / ".profiles.json.ab12_cd3.tmp"
    path.touch()
    old(path)
    assert locate_temporaries(tmp_path, [output]) == []
