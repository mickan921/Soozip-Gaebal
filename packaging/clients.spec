# Build on Ubuntu 24.04 amd64 using scripts/build_clients.py.
from pathlib import Path
root = Path(SPECPATH).parent
assets = root / 'build' / 'release-assets'
data = [(str(root / 'src/offline_apt_builder/resources'), 'offline_apt_builder/resources')]
binaries = [(str(assets / '7zz'), 'bin')]
worker = Analysis([str(root / 'packaging/worker_entry.py')], pathex=[str(root / 'src')],
                  binaries=binaries, datas=data, excludes=['PySide6'])
worker_pyz = PYZ(worker.pure)
worker_exe = EXE(worker_pyz, worker.scripts, [], exclude_binaries=True,
                 name='offline-apt-worker', console=True)
for name, module in (('Soozip', 'soozip'), ('Gaebal', 'gaebal')):
    app = Analysis([str(root / name / 'main.py')],
                   pathex=[str(root / 'src'), str(root / name / 'src')],
                   binaries=binaries, datas=data)
    # No TIFF input is used. The wheel's optional TIFF plugin requires the
    # libtiff.so.5 ABI, which Ubuntu 24.04 does not provide.
    app.binaries = [entry for entry in app.binaries if not entry[0].endswith('/imageformats/libqtiff.so')]
    pyz = PYZ(app.pure)
    exe = EXE(pyz, app.scripts, [], exclude_binaries=True, name=name, console=True)
    COLLECT(exe, worker_exe, app.binaries, app.datas, worker.binaries, worker.datas,
            name=name)
