"""Observe native Cognee multipart delivery unchanged; distinguish documents from messages."""
import hashlib,json,os
from email import policy
from email.parser import BytesParser
from pathlib import Path


def inspect_multipart(content_type,body):
    message=BytesParser(policy=policy.default).parsebytes(
        ('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+body)
    if not message.is_multipart():raise ValueError('Native Cognee payload is not multipart')
    fields={};files=[]
    for part in message.iter_parts():
        name=part.get_param('name',header='content-disposition');data=part.get_payload(decode=True)
        if part.get_filename():files.append({'field':name,'filename':part.get_filename(),
            'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        else:fields[name]=data.decode()
    return {'fields':fields,'files':files,'document_count':len(files)}


def observe(backend,root,source,text,manifest):
    original=backend._request;folder=Path(root)/'native-write-inputs';folder.mkdir(mode=0o700,exist_ok=True)
    def request(method,path,**kwargs):
        if method=='POST' and path=='/api/v1/remember':
            content_type,body=kwargs['multipart'];proof=inspect_multipart(content_type,body)
            digest=hashlib.sha256(text.encode()).hexdigest()
            if proof['document_count']!=1 or proof['files'][0]['sha256']!=digest:
                raise ValueError('Native Cognee write differs from the one submitted document')
            proof.update(version='cognee-native-write-input-v1',method=method,path=path,
                content_type=content_type,multipart_sha256=hashlib.sha256(body).hexdigest(),
                source_unit=Path(source).name,source_ids=manifest['source_ids'],source_messages=len(manifest['source_ids']),
                grouping='One supplied export document; no per-message add/cognify loop',
                conversation_sections_observed=sum(line.startswith('Conversation: ') for line in text.splitlines()),
                native_call_chain='HTTP remember -> add(data=[uploaded file]) -> cognify(datasets=[dataset]) -> native automatic improve')
            for suffix,data in [('multipart.bin',body),('json',(json.dumps(proof,indent=2)+'\n').encode())]:
                p=folder/(Path(source).name+'.'+suffix)
                with p.open('xb') as f:os.chmod(p,0o600);f.write(data)
        return original(method,path,**kwargs)
    backend._request=request
