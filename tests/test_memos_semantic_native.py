"""Opt-in, isolated pinned-native API test; local embeddings, zero LLM calls."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler,HTTPServer

import pytest

from provider_adapters.reme_memos_documents import ingest,request
from provider_adapters.memos_semantic_ready import RAW,SEMANTIC,proof_valid


@pytest.mark.skipif(os.environ.get('PACBENCH_TEST_NATIVE_MEMOS')!='1',reason='Explicit local native MemOS integration opt-in')
def test_pinned_native_repair_fills_vectors_without_extraction_or_paid_inference(tmp_path):
    from pacbench.provider_versions import verify_memos_package
    repo=Path(__file__).resolve().parents[1];verify_memos_package(repo)
    calls=[]
    class RejectLlm(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.path);self.send_error(503)
        def log_message(self,*a):pass
    llm=HTTPServer(('127.0.0.1',0),RejectLlm);thread=threading.Thread(target=llm.serve_forever,daemon=True);thread.start()
    home=tmp_path/'native-home';home.mkdir()
    config={'version':1,'llm':{'provider':'openai_compatible','endpoint':f'http://127.0.0.1:{llm.server_port}/v1','model':'offline-never-called','apiKey':'inert','fallbackToHost':False},
        'embedding':{'provider':'local','model':'Xenova/all-MiniLM-L6-v2','local':{'cacheDir':'/workspace/shared/pacbench-memos-models','offline':True}},'hub':{'enabled':False},'telemetry':{'enabled':False}}
    (home/'config.yaml').write_text(json.dumps(config))
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    # Allowlist the native process environment: never inherit a real key,
    # inference route, accounting token, host fallback, or NODE_OPTIONS hook.
    env={k:os.environ[k] for k in ('PATH','LANG','LD_LIBRARY_PATH','TMPDIR') if k in os.environ}
    env.update(HOME=str(tmp_path),MEMOS_HOME=str(home),TELEMETRY_ENABLED='false',HUB_ENABLED='false')
    endpoint=f'http://127.0.0.1:{port}';log=tmp_path/'native.log'
    with log.open('wb') as stream:
        process=subprocess.Popen(['node',str(repo/'provider_adapters/memos_service.mjs'),str(port)],cwd=tmp_path,env=env,stdout=stream,stderr=stream)
    try:
        deadline=time.monotonic()+30
        while time.monotonic()<deadline and process.poll() is None:
            try:request(endpoint,'/api/v1/health',None,.5);break
            except (OSError,ValueError):time.sleep(.1)
        else:pytest.fail('Isolated native service did not start: '+log.read_text()[-1200:])
        source=tmp_path/'source';source.mkdir();text='Synthetic original message: the test source is retained verbatim, without an assistant reply.\n'
        import hashlib
        sha=hashlib.sha256(text.encode()).hexdigest()
        (source/'messages.txt').write_text(text);(source/'source.json').write_text(json.dumps({'at':'2026-10-05T20:00:00-04:00','source_ids':['synthetic-message'],'document_sha256':sha}))
        raw=ingest('memos',source,endpoint);assert raw['ready'] and raw['ingestion_condition']==RAW
        db=home/'data/memos.db';native_id=raw['native_id']
        with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True) as conn:
            before=conn.execute('SELECT vec_summary,vec_action FROM traces WHERE id=?',(native_id,)).fetchone()
            counts={t:conn.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('traces','policies','world_model','skills')}
        assert before==(None,None)
        semantic=ingest('memos',source,endpoint,memos_condition=SEMANTIC,repair_timeout_s=30,max_repair_batches=3,memos_database=db)
        assert semantic['ready'],semantic
        assert proof_valid(semantic['semantic_readiness'],semantic['native_readback'],sha)
        with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True) as conn:
            dimensions=semantic['semantic_readiness']['after']['dimension']
            assert conn.execute('SELECT length(vec_summary),length(vec_action) FROM traces WHERE id=?',(native_id,)).fetchone()==(dimensions*4,dimensions*4)
            assert counts=={t:conn.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in counts}
        assert semantic['native_result']['duplicates']==1 and calls==[]
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        llm.shutdown();llm.server_close();thread.join(timeout=2)
