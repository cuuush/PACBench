import json
import pytest
from pacbench.hermes_gateway import _published_ready,GatewayLifecycleError


def test_partial_native_metadata_is_not_a_foreign_gateway(tmp_path,monkeypatch):
    monkeypatch.setattr('pacbench.hermes_gateway._start_time',lambda pid:5)
    (tmp_path/'gateway.pid').write_bytes(b'')
    assert _published_ready(tmp_path,2) is False
    record={'pid':2,'kind':'hermes-gateway','start_time':5,'hermes_home':str(tmp_path)}
    (tmp_path/'gateway.pid').write_text(json.dumps(record))
    (tmp_path/'gateway_state.json').write_text(json.dumps({**record,'gateway_state':'running'}))
    assert _published_ready(tmp_path,2) is True
    (tmp_path/'gateway.pid').write_text(json.dumps({**record,'pid':3}))
    with pytest.raises(GatewayLifecycleError,match='identity'):_published_ready(tmp_path,2)
