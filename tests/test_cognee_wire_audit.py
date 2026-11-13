import hashlib,json
from types import SimpleNamespace
from provider_adapters.cognee_wire_audit import observe


def test_one_four_message_conversation_stays_one_native_document(tmp_path):
    text='Conversation: thread-one\n'+'\n'.join(f'[2026-10-05T12:0{i}:00-04:00] Speaker {i%2}: message {i}' for i in range(4))
    body=('---x\r\nContent-Disposition: form-data; name="datasetName"\r\n\r\npacbench_unit\r\n'
          '---x\r\nContent-Disposition: form-data; name="run_in_background"\r\n\r\ntrue\r\n'
          '---x\r\nContent-Disposition: form-data; name="data"; filename="conversation.txt"\r\n'
          'Content-Type: text/plain\r\n\r\n'+text+'\r\n---x--\r\n').encode()
    calls=[];backend=SimpleNamespace(_request=lambda *a,**kw:calls.append((a,kw)))
    observe(backend,tmp_path,tmp_path/'unit',text,{'source_ids':['one','two','three','four']})
    backend._request('POST','/api/v1/remember',multipart=('multipart/form-data; boundary=-x',body),timeout=30)
    assert len(calls)==1 and calls[0][1]['multipart'][1] is body
    proof=json.loads((tmp_path/'native-write-inputs/unit.json').read_text())
    assert proof['document_count']==1 and proof['source_messages']==4
    assert proof['files'][0]['sha256']==hashlib.sha256(text.encode()).hexdigest()
    assert proof['fields']=={'datasetName':'pacbench_unit','run_in_background':'true'}
