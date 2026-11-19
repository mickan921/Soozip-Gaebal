"""Black-box X11/AT-SPI driver for installed, frozen clients."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pyatspi

EVIDENCE = Path('/evidence')
EVIDENCE.mkdir(exist_ok=True)


def descendants(node):
    yield node
    try:
        children = list(node)
    except Exception:
        children = []
    for child in children:
        yield from descendants(child)


def visible(node):
    try:
        return node.getState().contains(pyatspi.STATE_SHOWING)
    except Exception:
        return False


def nodes():
    return [node for node in descendants(pyatspi.Registry.getDesktop(0)) if visible(node)]


def dump():
    result = []
    for node in nodes():
        try:
            record = {'name': node.name, 'role': node.getRoleName()}
            record['bounds'] = list(node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS))
            try:
                record['text'] = node.queryText().getText(0, -1)
            except Exception:
                pass
            result.append(record)
        except Exception:
            pass
    return result


def wait(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.15)
    raise AssertionError('Timed out waiting for UI condition')


def find(name, role=None):
    return next((n for n in nodes() if n.name == name and (role is None or n.getRoleName() == role)), None)


def screenshot(name):
    subprocess.run(['scrot', '--overwrite', str(EVIDENCE / (name + '.png'))], check=True)
    (EVIDENCE / (name + '.json')).write_text(json.dumps(dump(), indent=2))


def click(node):
    action = node.queryAction()
    assert action.nActions, node.name
    assert action.doAction(0), node.name


def select(node, index):
    click(node)
    subprocess.run(['xdotool', 'key', 'Home', *(['Down'] * index), 'Return'], check=True)


def set_text(node, value):
    assert node.queryEditableText().setTextContents(value)


def text_nodes():
    return [n for n in nodes() if n.getRoleName() == 'text']


def enabled(name):
    node = find(name, 'push button')
    return node and node.getState().contains(pyatspi.STATE_ENABLED)


def table_rows():
    table = next(n for n in nodes() if n.getRoleName() == 'table').queryTable()
    return [[table.getAccessibleAt(r, c).name for c in range(table.nColumns)] for r in range(table.nRows)]


def soozip(mode):
    select(find('APT packages', 'combo box'), 1)
    wait(lambda: find('Import requirements.txt…', 'push button'))
    assert find('3.12', 'combo box')
    fields = text_nodes()
    set_text(fields[0], 'requests\nnumpy\nhttpx[http2,cli]\ntomli; python_version < "3.12"')
    set_text(fields[1], '/evidence/bundles')
    screenshot('soozip-pip-ready')
    if mode.endswith('inspect'):
        return
    # Exercise the actual installed GUI, its frozen worker, and a real resolver.
    click(find('Build and Validate Bundle', 'push button'))
    wait(lambda: not find('Build and Validate Bundle', 'push button').getState().contains(pyatspi.STATE_ENABLED))
    screenshot('soozip-building')
    wait(lambda: find('Build and Validate Bundle', 'push button').getState().contains(pyatspi.STATE_ENABLED), 900)
    screenshot('soozip-complete')
    reports = list(Path('/evidence/bundles').glob('*.report.json'))
    assert len(reports) == 1, dump()
    report = json.loads(reports[0].read_text())
    assert report['status'] == 'VALIDATED', report
    archives = list(Path('/evidence/bundles').glob('*.7z'))
    assert len(archives) == 1
    (EVIDENCE / 'result.json').write_text(json.dumps({'status': 'PASSED', 'archive': str(archives[0]), 'uid': os.getuid(), 'report': report}, indent=2))
    print('SOOZIP_DESKTOP_ACCEPTED', flush=True)


def gaebal(mode):
    archives = list(Path('/input').glob('*.7z'))
    assert len(archives) == 1
    expected_count = json.loads(next(Path('/input').glob('*.report.json')).read_text())['package_count']
    click(find('Open .7z Bundle…', 'push button'))
    wait(lambda: find('Open bundle', 'file chooser') or find('Open bundle', 'dialog'))
    # Qt's file dialog focuses its filename editor on open.
    subprocess.run(['xdotool', 'type', '--clearmodifiers', str(archives[0])], check=True)
    subprocess.run(['xdotool', 'key', 'Return'], check=True)
    wait(lambda: find('Create virtualenv', 'combo box'))
    wait(lambda: find('Review Python Package Changes', 'push button').getState().contains(pyatspi.STATE_ENABLED))
    screenshot('gaebal-verified')
    if mode.endswith('inspect'):
        return
    click(find('Python executable…', 'push button'))
    wait(lambda: find('Select installed Python executable', 'file chooser') or
         find('Select installed Python executable', 'dialog'))
    subprocess.run(['xdotool', 'type', '--clearmodifiers', '/usr/bin/python3'], check=True)
    subprocess.run(['xdotool', 'key', 'Return'], check=True)
    wait(lambda: find('/usr/bin/python3', 'combo box'))
    destination = Path('/home/qa/qa environment')
    set_text(text_nodes()[1], str(destination))
    click(find('Review Python Package Changes', 'push button'))
    wait(lambda: enabled('Install Reviewed Packages…'))
    assert not destination.exists(), 'Preview created the environment before approval'
    rows = table_rows()
    assert len(rows) == expected_count and all(row[1] == 'install' for row in rows), rows
    screenshot('gaebal-preview')
    click(find('Install Reviewed Packages…', 'push button'))
    wait(lambda: not enabled('Review Python Package Changes'))
    assert not enabled('Cancel'), 'Active installation can be cancelled'
    screenshot('gaebal-installing')
    # Ask the window manager to close the active window during installation.
    subprocess.run(['xdotool', 'key', 'alt+F4'], check=True)
    wait(lambda: find('Keep this window open until installation finishes.', 'label'))
    screenshot('gaebal-close-protected')
    click(find('OK', 'push button'))
    wait(lambda: find('INSTALLED', 'label') and enabled('Review Python Package Changes'), 180)
    screenshot('gaebal-installed')
    (EVIDENCE / 'installation.log').write_text(text_nodes()[-1].queryText().getText(0, -1))
    assert destination.stat().st_uid == os.getuid() != 0
    check = subprocess.run([str(destination / 'bin/python'), '-I', '-c',
        'import os,requests,numpy,httpx,h2,rich,click,importlib.util; '
        'assert numpy.arange(3).sum() == 3; assert not importlib.util.find_spec("tomli"); '
        'assert os.getuid() != 0; print("GUI_ENVIRONMENT_ACCEPTED")'], check=True, text=True, capture_output=True)
    cli = subprocess.run([str(destination / 'bin/httpx'), '--help'], check=True, text=True, capture_output=True)
    (EVIDENCE / 'environment-check.log').write_text(check.stdout + cli.stdout)
    before = {str(p): p.stat().st_mtime_ns for p in destination.rglob('*') if p.is_file()}
    select(find('Create virtualenv', 'combo box'), 1)
    wait(lambda: find('Use existing virtualenv', 'combo box'))
    assert not enabled('Install Reviewed Packages…')
    click(find('Review Python Package Changes', 'push button'))
    wait(lambda: find('Already Installed', 'push button') and enabled('Review Python Package Changes'))
    assert not enabled('Already Installed')
    rows = table_rows()
    assert len(rows) == expected_count and all(row[1] == 'Already installed' for row in rows), rows
    assert before == {str(p): p.stat().st_mtime_ns for p in destination.rglob('*') if p.is_file()}
    screenshot('gaebal-already-installed')
    (EVIDENCE / 'result.json').write_text(json.dumps({'status': 'PASSED', 'uid': os.getuid(),
        'python': subprocess.check_output(['/usr/bin/python3', '--version'], text=True).strip(),
        'environment': str(destination), 'packages': len(rows), 'network': 'none',
        'close_protection': True, 'repeat_noop': True}, indent=2))
    print('GAEBAL_DESKTOP_ACCEPTED', flush=True)


def main():
    mode = sys.argv[1]
    app = 'Soozip' if mode.startswith('soozip') else 'Gaebal'
    source = Path('/releases') / (app + '-ubuntu24.04-amd64.tar.gz')
    subprocess.run(['sha256sum', '-c', source.name + '.sha256'], cwd=source.parent, check=True)
    subprocess.run(['tar', '-xzf', str(source), '-C', '/home/qa'], check=True)
    command = ['bash', f'/home/qa/{app}/install.sh']
    if app == 'Soozip':
        command.append('--skip-docker-setup')
    subprocess.run(command, check=True)
    with (EVIDENCE / (app.lower() + '-app.log')).open('w') as log:
        process = subprocess.Popen([f'/home/qa/.local/bin/{app.lower()}'], stdout=log, stderr=subprocess.STDOUT)
        try:
            wait(lambda: find(app, 'frame'))
            screenshot(app.lower() + '-initial')
            if app == 'Soozip':
                soozip(mode)
            else:
                gaebal(mode)
            print(json.dumps(dump(), indent=2), flush=True)
        except BaseException:
            screenshot(app.lower() + '-failure')
            raise
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()
