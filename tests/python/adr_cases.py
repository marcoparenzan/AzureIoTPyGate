# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
import os

# Legacy environment names remain accepted for one release.
if not os.environ.get('PYGATE_SOAK_AGENT') and os.environ.get('FORGE_SOAK_AGENT'):
    os.environ['PYGATE_SOAK_AGENT'] = os.environ['FORGE_SOAK_AGENT']
if not os.environ.get('PYGATE_PAHO_SITE') and os.environ.get('FORGE_PAHO_SITE'):
    os.environ['PYGATE_PAHO_SITE'] = os.environ['FORGE_PAHO_SITE']
if not os.environ.get('PYGATE_TEST_KEY') and os.environ.get('FORGE_TEST_KEY'):
    os.environ['PYGATE_TEST_KEY'] = os.environ['FORGE_TEST_KEY']
if not os.environ.get('PYGATE_TEST_SCRATCH') and os.environ.get('FORGE_DEVICE_TEST_SCRATCH'):
    os.environ['PYGATE_TEST_SCRATCH'] = os.environ['FORGE_DEVICE_TEST_SCRATCH']
if not os.environ.get('PYGATE_WORKER_HEARTBEAT_TIMEOUT_SEC') and os.environ.get('FORGE_WORKER_HEARTBEAT_TIMEOUT_SEC'):
    os.environ['PYGATE_WORKER_HEARTBEAT_TIMEOUT_SEC'] = os.environ['FORGE_WORKER_HEARTBEAT_TIMEOUT_SEC']
if not os.environ.get('PYGATE_TEST_CERT') and os.environ.get('FORGE_TEST_CERT'):
    os.environ['PYGATE_TEST_CERT'] = os.environ['FORGE_TEST_CERT']
if not os.environ.get('PYGATE_ADR_FIXTURE_ROOT') and os.environ.get('FORGE_ADR_FIXTURE_ROOT'):
    os.environ['PYGATE_ADR_FIXTURE_ROOT'] = os.environ['FORGE_ADR_FIXTURE_ROOT']
import json
import ssl
import time
from dps import Provisioner, issued_bundle, verified_https_connection
from mqtt_transport import LoopbackTransport
from config import load_config, StateStore


def fixture():
    root = os.environ.get('PYGATE_ADR_FIXTURE_ROOT', 'D:/Dev/BuildArtifacts/AzureIoTPyGate/tmp/step02/xunit/test_adr_chain_encodings')
    with open(os.path.join(root, 'adr-fixture.json')) as f:
        return root, json.load(f)


def adr_config(root):
    return {'identityFamily': 'X509DpsAdr', 'registrationId': 'offline-test', 'idScope': 'offline-scope', 'globalEndpoint': 'never-contacted.invalid', 'allowDpsRegistration': True, 'deviceKey': os.path.join(root, 'device-key.pem'), 'csr': os.path.join(root, 'device.csr.pem'), 'bootstrapCert': 'unused', 'bootstrapKey': 'unused', 'cert': 'unused', 'key': 'unused', 'stateFile': os.path.join(root, 'adr-state.json'), 'payloadDir': os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'src', 'payloads')}


def assigned(items):
    return {'status': 'assigned', 'registrationState': {'assignedHub': 'offline-hub.invalid', 'deviceId': 'offline-test', 'issuedCertificateChain': items}}


class Clock:
    def __init__(self):
        self.value = 0
    def now(self):
        return self.value
    def sleep(self, seconds):
        self.value += seconds


def expect_error(action):
    failed = False
    try:
        action()
    except Exception:
        failed = True
    assert failed


def test_adr_chain_encodings():
    root, data = fixture()
    for encoding in ['pem', 'base64Pem', 'base64Der']:
        path = issued_bundle(data[encoding], os.path.join(root, 'device-key.pem'), root)
        with open(path) as f:
            assert f.read().startswith(data['pem'][2])
    with open(path, 'rb') as f:
        before = f.read()
    expect_error(lambda: issued_bundle(data['base64Pem'], os.path.join(root, 'wrong-key.pem'), root))
    expect_error(lambda: issued_bundle([data['pem'][2], data['pem'][2]], os.path.join(root, 'device-key.pem'), root))
    expect_error(lambda: issued_bundle([], os.path.join(root, 'device-key.pem'), root))
    with open(path, 'rb') as f:
        assert before == f.read()


def test_adr_register_cache_renewal():
    root, data = fixture()
    c = adr_config(root)
    clock = Clock()
    t = LoopbackTransport()
    t.adr_responses = [(202, {'status': 'assigning', 'operationId': 'op/a'}, '3'), (200, assigned(data['base64Pem']), None)]
    state = {}
    p = Provisioner(c, state, t, clock.now, clock.sleep)
    assert p.register() == ('offline-hub.invalid', 'offline-test')
    assert [r[0] for r in t.adr_requests] == ['PUT', 'GET']
    assert '/register?api-version=2026-11-01' in t.adr_requests[0][1]
    assert 'op%2Fa' in t.adr_requests[1][1]
    body = json.loads(t.adr_requests[0][2])
    assert 'BEGIN' not in body['csr'] and '\n' not in body['csr']
    assert clock.value == 3 and t.certificate[0] == state['issuedCert']
    with open(os.path.join(root, 'dps-registration-raw.json'), 'rb') as f:
        assert json.loads(f.read().decode('utf-8'))['status'] == 'assigned'
    assert p.register() == ('offline-hub.invalid', 'offline-test')
    assert len(t.adr_requests) == 2
    state['adrIssuedAt'] = time.time() - 61 * 86400
    assert p.renewal_due()
    t.adr_responses = [(200, assigned(data['pem']), None)]
    p.register()
    assert len(t.adr_requests) == 3 and not p.renewal_due()
    p.clear_assignment()
    assert 'assignedHub' not in state


def test_adr_errors_and_single_put():
    root, data = fixture()
    for status, body in [(400, {'errorMessage': 'bad CSR'}), (429, {}), (500, {}), (200, {'status': 'failed'}), (202, {'status': 'assigning'}), (200, assigned([]))]:
        t = LoopbackTransport()
        t.adr_responses = [(status, body, None)]
        state = {}
        p = Provisioner(adr_config(root), state, t)
        expect_error(p.register)
        assert len(t.adr_requests) == 1 and not state
    t = LoopbackTransport()
    c = adr_config(root)
    c['allowDpsRegistration'] = False
    expect_error(Provisioner(c, {}, t).register)
    assert not t.adr_requests
    c['allowDpsRegistration'] = True
    clock = Clock()
    t.adr_responses = [(202, {'status': 'assigning', 'operationId': 'op'}, '60')]
    expect_error(lambda: Provisioner(c, {}, t, clock.now, clock.sleep).register(timeout=1))
    assert len(t.adr_requests) == 1


def test_adr_context_and_config():
    class Sentinel:
        check_hostname = True
        verify_mode = ssl.CERT_REQUIRED
    class FakeConnection:
        def __init__(self, host, port, context=None, timeout=None):
            self._context = None  # Model PySharp constructor bug.
        def connect(self):
            assert self._context is context, 'Supplied context was ignored'
    context = Sentinel()
    verified_https_connection('never-contacted.invalid', context, factory=FakeConnection).connect()
    expect_error(lambda: FakeConnection('unused', 443, context=context).connect())
    context.check_hostname = False
    expect_error(lambda: verified_https_connection('unused', context, factory=FakeConnection))
    root, data = fixture()
    c = adr_config(root)
    path = os.path.join(root, 'config-test.json')
    for field in ['deviceKey', 'csr']:
        c[field] = os.path.basename(c[field])
    with open(path, 'w') as f:
        json.dump(c, f)
    loaded = load_config(path)
    assert loaded['deviceKey'] == os.path.join(root, 'device-key.pem')
    del c['csr']
    with open(path, 'w') as f:
        json.dump(c, f)
    expect_error(lambda: load_config(path))
    c['identityFamily'] = 'bogus'
    expect_error(lambda: Provisioner(c, {}, LoopbackTransport()).register())


def test_adr_agent_hub_rejection():
    from main import Agent
    root, data = fixture()
    clock = Clock()
    config = adr_config(root)
    store = StateStore(os.path.join(root, 'agent-state.json'))
    store.data = {}
    t = LoopbackTransport()
    t.adr_responses = [(200, assigned(data['base64Pem']), None)]
    agent = Agent(config, store, t, clock.now, clock.sleep)
    agent.start()
    assert t.certificate[1] == config['deviceKey'] and t.connected
    t.adr_responses = [(200, assigned(data['base64Pem']), None)]
    t.force_disconnect('unauthorized')
    agent.step()
    assert len(t.adr_requests) == 2 and t.connected and not agent.reprovision
    store.data['adrIssuedAt'] = time.time() - 61 * 86400
    t.adr_responses = [(200, assigned(data['base64Pem']), None)]
    agent.step()
    assert len(t.adr_requests) == 3
    agent.stop()


def test_adr_expiry_metadata():
    import hashlib
    from dps import certificate_renewal_at
    root, data = fixture()
    cert = issued_bundle(data['base64Pem'], os.path.join(root, 'device-key.pem'), root)
    c = adr_config(root)
    issued_at = time.time()
    c['certificateExpiryFile'] = os.path.join(root, 'missing-expiry.json')
    assert certificate_renewal_at(cert, issued_at, c) == issued_at + 60 * 86400
    with open(os.path.join(root, 'fixture-leaf.expiry.json')) as f:
        metadata = json.load(f)
    with open(cert, 'rb') as f:
        metadata['certificateSha256'] = hashlib.sha256(f.read()).hexdigest()
    c['certificateExpiryFile'] = os.path.join(root, 'expiry-test.json')
    with open(c['certificateExpiryFile'], 'w') as f:
        json.dump(metadata, f)
    from datetime import datetime, timezone
    text = metadata['notAfter']
    expected = datetime(int(text[0:4]), int(text[5:7]), int(text[8:10]), int(text[11:13]), int(text[14:16]), int(text[17:19]), tzinfo=timezone.utc).timestamp()
    assert certificate_renewal_at(cert, issued_at, c) == expected - 7 * 86400
    state = {'issuedCert': cert, 'adrIssuedAt': issued_at, 'adrRenewAt': issued_at + 60 * 86400}
    p = Provisioner(c, state, LoopbackTransport())
    assert not p.renewal_due()
    metadata['notAfter'] = '2000-01-01T00:00:00Z'
    with open(c['certificateExpiryFile'], 'w') as f:
        json.dump(metadata, f)
    assert p.renewal_due()
    metadata['notAfter'] = 'bad-time'
    with open(c['certificateExpiryFile'], 'w') as f:
        json.dump(metadata, f)
    expect_error(p.renewal_due)
    metadata['certificateSha256'] = 'stale'
    with open(c['certificateExpiryFile'], 'w') as f:
        json.dump(metadata, f)
    assert certificate_renewal_at(cert, issued_at, c) == issued_at + 60 * 86400
    # Real metadata can arrive after registration: the next check reloads it.
    with open(cert, 'rb') as f:
        metadata['certificateSha256'] = hashlib.sha256(f.read()).hexdigest()
    metadata['notAfter'] = text
    with open(c['certificateExpiryFile'], 'w') as f:
        json.dump(metadata, f)
    assert not p.renewal_due() and state['adrRenewAt'] == expected - 7 * 86400


ADR_TESTS = [test_adr_chain_encodings, test_adr_register_cache_renewal, test_adr_errors_and_single_put, test_adr_context_and_config, test_adr_agent_hub_rejection, test_adr_expiry_metadata]
