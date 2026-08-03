import os
from pathlib import Path

project_root = Path(SPECPATH).resolve().parents[1]
source_root = project_root / "src"
entry_point = source_root / "data_mask_studio" / "__main__.py"
version_file = os.environ.get("DMS_VERSION_FILE")
icon_value = os.environ.get("DMS_ICON_FILE")
icon_path = Path(icon_value) if icon_value else None

datas = []
if icon_path is not None and icon_path.is_file():
    datas.append((str(icon_path), "assets"))

analysis = Analysis(
    [str(entry_point)],
    pathex=[str(source_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest", "tkinter", "tests"],
    noarchive=False,
    optimize=1,
)

# Metadado criado por instalações editáveis; pode revelar o caminho local do
# repositório e não é necessário para consultar a versão do pacote.
analysis.datas = [
    item for item in analysis.datas if Path(item[0]).name != "direct_url.json"
]

python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="DataMaskStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path is not None and icon_path.is_file() else None,
    version=version_file,
)

portable = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DataMaskStudio",
)
