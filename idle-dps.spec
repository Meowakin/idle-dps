# Build: pyinstaller idle-dps.spec  ->  dist/idle-dps.exe (single file)
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["run_idle_dps.py"],
    datas=[("idle_dps/static", "idle_dps/static")],
    # exporters are imported lazily by the SDK; make sure they are bundled
    hiddenimports=collect_submodules("opentelemetry.exporter.otlp.proto.http"),
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="idle-dps", console=True, upx=False)
