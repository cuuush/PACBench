"""Read-only attribution of native persisted Q&A to earlier observed turns."""
import hashlib,json,sqlite3,sys,urllib.parse
from pathlib import Path


def captures(root,prior):
    root=Path(root);sys.path.insert(0,str(root/'hermes-home/plugins/cognee'))
    from cognee_integration_hermes import memory_credit,recall_notice
    from cognee_integration_hermes.provider import _safe_session_component
    known={}
    for path in [*(root/'turns').glob('*/turn.json'),*(root/'turns').glob('*/followups/combined/turn.json')]:
        turn=json.loads(path.read_text())
        if turn.get('returncode') or not turn.get('answer') or not turn.get('session_id'):continue
        answer=memory_credit.strip_credit_lines(recall_notice.strip_notice(turn['answer']))
        sid='hermes_'+_safe_session_component(turn['session_id'])
        text=f"Session ID: {sid}\n\nQuestion: {turn['prompt']}\n\nAnswer: {answer}\n\n"
        known[hashlib.sha256(text.encode()).hexdigest()]={'turn_file':str(path.relative_to(root)),
            'turn_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'session_id':turn['session_id']}
    sources={p['data_id'].replace('-','') for p in prior};result=[]
    db=root/'cognee-system/databases/cognee_db'
    with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as connection:
        for ident,metadata,location in connection.execute('SELECT id,external_metadata,raw_data_location FROM data'):
            if ident.replace('-','') in sources:continue
            meta=json.loads(metadata) if metadata else {}
            path=Path(urllib.parse.urlparse(location).path).resolve()
            if not path.is_relative_to((root/'cognee-data').resolve()):raise ValueError('Native capture source path escaped store')
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            if meta.get('node_set')!=['user_sessions_from_cache'] or sha not in known:
                raise ValueError('Unattributed native dataset document; do not ingest another source')
            from uuid import UUID
            result.append({'data_id':str(UUID(ident)),'source_sha256':sha,**known[sha],
                'protocol':'cognee-native-observed-turn-qa-v1'})
    return result
