"""Validate the deployment isolation contract (requires Docker Compose)."""
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
config = json.loads(subprocess.check_output([
    'docker', 'compose', '--env-file', 'deploy/.env.example',
    '-f', 'deploy/compose.yaml', 'config', '--format', 'json',
], cwd=root))
assert config['name'] == 'aftersales'
services = config['services']
assert set(services) == {'db', 'api', 'web'}
assert config['networks']['database']['internal'] is True
assert set(services['db']['networks']) == {'database'}
assert 'edge' not in services['api']['networks']
assert 'database' not in services['web']['networks']
for name, service in services.items():
    assert 0 < float(service['cpus']) <= 0.75
    assert 0 < int(service['mem_limit']) <= 768 * 1024**2
    assert service['memswap_limit'] == service['mem_limit']
    assert service['pids_limit'] > 0
    assert service['logging']['options'] == {'max-size': '10m', 'max-file': '3'}
    assert service.get('network_mode') != 'host'
    assert not service.get('privileged', False)
    assert service.get('restart') == 'unless-stopped'
    if name != 'web':
        assert not service.get('ports')
    for mount in service.get('volumes', []):
        assert mount['type'] == 'volume'
        assert mount['source'] in config['volumes']
assert services['web']['ports'][0]['host_ip'] == '127.0.0.1'
assert services['api']['read_only'] and services['web']['read_only']
assert 'ALL' in services['api']['cap_drop']
print('Deployment isolation checks passed')
