"""The selected native v5 namespace and document-status API, without inference."""
from __future__ import annotations


def namespace(plugin, home):
    config = plugin._load_supermemory_config(str(home))
    tag = plugin._sanitize_tag(plugin._expand_container_tag(config['container_tag'], 'default'))
    if not config['base_url'].startswith('http://127.0.0.1:'):
        raise ValueError('Supermemory requires the run-owned local server')
    return config, tag


def document(client, tag, identifier):
    """Keep the native response; read readiness from its documented system field."""
    import urllib.parse
    value = client.documents.get(tag, urllib.parse.quote(identifier, safe='')).model_dump(mode='json')
    if value.get('id') != identifier or not isinstance(value.get('system'), dict):
        raise ValueError('Supermemory native document identity or system status missing')
    system = value['system']
    return value, {'id': value['id'], 'status': system.get('status'),
                   'dreaming_status': system.get('dreaming_status', system.get('dreamingStatus'))}
