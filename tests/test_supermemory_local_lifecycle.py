from contextlib import nullcontext
from io import StringIO
import json
import signal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pacbench import import_lifecycle, openrouter_direct
from provider_adapters import supermemory_local as adapter


@pytest.fixture
def native_service(tmp_path, monkeypatch):
    runtime = tmp_path / 'vendor/supermemory-local'
    runtime.mkdir(parents=True)
    (runtime / 'supermemory-server-linux-x64').touch()
    (runtime / 'installation.json').write_text('{}')
    root = tmp_path / 'run/supermemory'
    (root / 'hermes-home').mkdir(parents=True)
    hooks = []
    process = Mock(pid=900001)
    process.poll.return_value = None

    def start_process(*_, **options):
        assert options['start_new_session'] is True
        options['stdout'].write(b'API key test\n')
        options['stdout'].flush()
        return process

    def wait_for_exit(*, timeout):
        process.poll.return_value = 0

    process.wait.side_effect = wait_for_exit
    popen = Mock(side_effect=start_process)
    kill_group = Mock()
    scan = Mock(return_value=([], 0))
    cleanup = Mock(return_value={'state': 'verified-inventory-stopped'})
    monkeypatch.setattr(adapter, '__file__', str(tmp_path / 'provider_adapters/supermemory_local.py'))
    monkeypatch.delenv('PACBENCH_SUPERMEMORY_STOCK_SOURCE', raising=False)
    monkeypatch.setattr(adapter, 'validate_route', lambda *_: None)
    monkeypatch.setattr(openrouter_direct, 'active', lambda: False)
    monkeypatch.setattr(adapter.atexit, 'register', hooks.append)
    monkeypatch.setattr(adapter.atexit, 'unregister', hooks.remove)
    monkeypatch.setattr(adapter.subprocess, 'Popen', popen)
    monkeypatch.setattr(adapter.os, 'killpg', kill_group)
    monkeypatch.setattr(adapter.socket, 'socket', lambda: nullcontext(Mock(getsockname=lambda: ('127.0.0.1', 1234))))
    monkeypatch.setattr(adapter.socket, 'create_connection', lambda *_, **__: nullcontext())
    monkeypatch.setattr(adapter.urllib.request, 'urlopen', lambda *_, **__: StringIO('{"memories": []}'))
    monkeypatch.setattr(import_lifecycle, 'scan', scan)
    monkeypatch.setattr(import_lifecycle, 'cleanup', cleanup)
    env = {}
    return SimpleNamespace(start=lambda: adapter.start(root, env, 'offline', 'offline', 'offline'),
                           root=root, env=env, process=process, popen=popen, kill_group=kill_group,
                           hooks=hooks, scan=scan, cleanup=cleanup)


@pytest.mark.parametrize('artifact', ['native-server-setup.json', 'supermemory.json'])
def test_supermemory_startup_failure_immediately_stops_owned_service(native_service, monkeypatch, artifact):
    service = native_service
    original_save = adapter.save
    failure = OSError('synthetic setup write error')

    def save(path, value):
        if path.name == artifact:
            raise failure
        original_save(path, value)

    monkeypatch.setattr(adapter, 'save', save)
    with pytest.raises(OSError) as observed:
        service.start()
    assert observed.value is failure
    service.kill_group.assert_called_once_with(service.process.pid, signal.SIGTERM)
    owner = service.popen.call_args.kwargs['env'][import_lifecycle.OWNER]
    service.cleanup.assert_called_once_with(service.root, owner, known=[])
    assert service.hooks == [] and service.env == {}
    assert (service.root / 'native-service-stop.json').is_file()


def test_supermemory_scan_failure_still_signals_own_group_and_keeps_retry(native_service):
    service = native_service
    stop = service.start()
    scan_failure = OSError('inventory read failed')
    service.scan.side_effect = scan_failure
    service.cleanup.side_effect = PermissionError('cleanup read failed')
    with pytest.raises(OSError) as observed:
        stop()
    assert observed.value is scan_failure
    service.kill_group.assert_called_once_with(service.process.pid, signal.SIGTERM)
    assert service.hooks == [stop]
    path = service.root / 'native-service-stop.json'
    assert json.loads(path.read_text()) == {'state': 'cleanup-unverified', 'shutdown_errors': [
        {'stage': 'process_inventory', 'failure_type': 'OSError'},
        {'stage': 'process_cleanup', 'failure_type': 'PermissionError'},
    ]}

    service.scan.side_effect = service.cleanup.side_effect = None
    service.hooks[0]()
    stop()
    receipt = json.loads(path.read_text())
    assert receipt['state'] == 'verified-inventory-stopped'
    assert [error['stage'] for error in receipt['shutdown_errors']] == ['process_inventory', 'process_cleanup']
    assert service.hooks == [] and service.cleanup.call_count == 2
    service.kill_group.assert_called_once()


def test_supermemory_receipt_failure_retries_write_without_repeating_cleanup(native_service, monkeypatch):
    service = native_service
    stop = service.start()
    assert service.env['SUPERMEMORY_API_KEY'] == 'test'
    original_save = adapter.save
    receipt_write = Mock(side_effect=OSError('receipt write failed'))
    monkeypatch.setattr(adapter, 'save', receipt_write)
    with pytest.raises(OSError, match='receipt write failed'):
        stop()
    assert service.hooks == [stop]
    receipt_write.side_effect = original_save
    service.hooks[0]()
    stop()
    assert receipt_write.call_count == 2 and service.hooks == []
    service.cleanup.assert_called_once()
    service.kill_group.assert_called_once()
    assert json.loads((service.root / 'native-service-stop.json').read_text())['shutdown_errors'] == [
        {'stage': 'receipt_write', 'failure_type': 'OSError'},
    ]


def test_supermemory_escalates_only_while_direct_child_is_unreaped(native_service):
    from subprocess import TimeoutExpired
    from unittest.mock import call

    service = native_service
    stop = service.start()

    def wait(*, timeout):
        if timeout == 15:
            raise TimeoutExpired('offline', timeout)
        service.process.poll.return_value = -signal.SIGKILL

    service.process.wait.side_effect = wait
    stop()
    stop()
    assert service.kill_group.call_args_list == [
        call(service.process.pid, signal.SIGTERM), call(service.process.pid, signal.SIGKILL),
    ]
    assert service.hooks == []


def test_supermemory_early_exit_uses_inventory_without_signaling_a_reaped_pid(native_service):
    service = native_service
    service.process.poll.return_value = 1
    with pytest.raises(RuntimeError, match='startup did not produce its native API key'):
        service.start()
    service.kill_group.assert_not_called()
    service.cleanup.assert_called_once()
    assert service.hooks == []
