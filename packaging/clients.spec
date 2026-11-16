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
for role in ('online', 'offline'):
    app = Analysis([str(root / f'packaging/{role}_entry.py')], pathex=[str(root / 'src')],
                   binaries=binaries, datas=data)
    pyz = PYZ(app.pure)
    exe = EXE(pyz, app.scripts, [], exclude_binaries=True, name=f'offline-apt-{role}', console=True)
    COLLECT(exe, worker_exe, app.binaries, app.datas, worker.binaries, worker.datas,
            name=f'offline-apt-{role}')
