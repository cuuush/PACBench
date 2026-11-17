"""Verify full source delivery through native read-only IMAP and archive shaping."""
from __future__ import annotations
import argparse,hashlib,json,sqlite3,uuid
from datetime import datetime
from email.utils import parsedate_to_datetime,parseaddr,getaddresses
from pathlib import Path
from types import SimpleNamespace
from .email_fixture import MailboxServer,mail_address
from .imessage_fixture import identity
from .provider_versions import verify_memcal_source,memcal_source


def audit(component):
    repo=Path(__file__).resolve().parents[1];verify_memcal_source(repo)
    from memcal.sources import proton
    from memcal import textclean
    if not Path(proton.__file__).resolve().is_relative_to(memcal_source(repo).resolve()):
        raise ValueError('Wrong native Memcal module')
    component=Path(component).resolve();root=component/'repeat-1/memcal'
    source=[m for path in sorted((component/'sources').glob('*/messages.json')) for m in json.loads(path.read_text())]
    if len({m['source_id'] for m in source})!=len(source):raise ValueError('Duplicate source identity')
    with sqlite3.connect((root/'memcal-home/memcal.db').as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;archive=[dict(r) for r in db.execute('select * from archive')]
    if len(archive)!=len(source):raise ValueError('Archive count differs from full source')
    native={(r['channel'],r['external_id']):r for r in archive}
    if len(native)!=len(archive):raise ValueError('Duplicate native archive identity')
    checks=[];mailboxes=sorted((component/'sources').glob('*/mailbox.json'));mailbox=mailboxes[-1]
    fixture=json.loads(mailbox.read_text())['messages'];email_sources={m['source_id']:m for m in source if m['channel']=='email'}
    if {r['source']['source_id'] for r in fixture}!=set(email_sources):raise ValueError('Mailbox omits source email')
    with MailboxServer(mailbox) as server:
        secrets={'PROTON_BRIDGE_HOST':'127.0.0.1','PROTON_BRIDGE_PORT':str(server.port),'PROTON_BRIDGE_USER':server.user,'PROTON_BRIDGE_PASSWORD':server.password}
        cfg=SimpleNamespace(secret=lambda name,alias:secrets.get(name))
        with proton.Bridge(cfg) as bridge:
            for folder in ['Sent','INBOX']:
                ok,_=bridge.select(folder)
                if not ok:raise ValueError('Native mailbox select failed')
                expected=[r for r in fixture if r['folder']==folder]
                if bridge.uids_since(0)!=[r['uid'] for r in expected]:raise ValueError('Native mailbox UID coverage differs')
                for row in expected:
                    m=row['source'];message=bridge.message(row['uid']);raw=proton.extract_text(message)
                    if raw.replace('\r\n','\n').strip()!=m['text'].strip():raise ValueError('Native reader did not receive complete email body')
                    if parsedate_to_datetime(message['Date'])!=datetime.fromisoformat(m['written_at']):raise ValueError('Source email time changed')
                    if parseaddr(message['From'])[1]!=mail_address(m['sender']):raise ValueError('Source email author changed')
                    recipients={addr for _,addr in getaddresses(message.get_all('To',[]))};wanted={mail_address(p) for p in m['structured']['participants'] if p!=m['sender']}
                    if recipients!=wanted:raise ValueError('Source email recipients changed')
                    key=('email',row['message_id'].strip('<>'));a=native[key];shaped,meta=proton._shape_body(raw,str(message['Subject']))
                    if shaped!=a['text'] or bool(a['from_me'])!=m['from_owner'] or datetime.fromisoformat(a['ts'])!=datetime.fromisoformat(m['written_at']):raise ValueError('Stored native email differs from stock reader output')
                    quotes=textclean.strip_quotes(raw)!=raw
                    urls=any(textclean.shorten_url(match)!=match.group(0) for match in textclean.URL.finditer(raw))
                    quoted=[line.lstrip()[1:].strip() for line in raw.splitlines() if line.lstrip().startswith('>')]
                    prior=[s['source_id'] for s in source if s['thread']==m['thread'] and s['written_at']<m['written_at'] and s['text'].strip() in quoted]
                    checks.append({'source_id':m['source_id'],'rfc822_sha256':hashlib.sha256(row['rfc822'].encode()).hexdigest(),'native_archive_id':a['id'],'native_reader_received_full_body':True,'envelope_author_time_recipients_verified':True,'native_archive_shape_exact':True,'body_chars':meta['body_chars'],'kept_chars':meta['kept_chars'],'native_body_truncated_flag':bool(meta.get('body_truncated')),'quote_history_removed':quotes,'native_url_normalization':urls,'length_limit_reached':len(raw)>=min(proton.BODY_CHARS,proton.ARCHIVE_BODY_CHARS),'quoted_text_duplicates_prior_source_ids':prior})
        commands=server.commands
    for m in source:
        if m['channel']!='imessage':continue
        identifier=str(uuid.uuid5(uuid.NAMESPACE_URL,'pacbench-source-message:'+m['source_id'])).upper();a=native[('imessage',identifier)]
        if a['text']!=m['text'] or bool(a['from_me'])!=m['from_owner'] or datetime.fromisoformat(a['ts'])!=datetime.fromisoformat(m['written_at']):raise ValueError('Stored native iMessage differs from original')
        if not m['from_owner'] and a['handle']!=identity(m['sender'])['handle']:raise ValueError('Native source speaker changed')
    if any(c['length_limit_reached'] for c in checks):raise ValueError('Length limit reached; inspect before qualification')
    return {'version':'memcal-native-input-cause-audit-v1','state':'pass','source_messages_verified':len(source),'native_imessage_bodies_times_owners_incoming_speakers_verified':len(source)-len(checks),'native_tls_imap_emails_verified':len(checks),'native_email_transport':'Stock Bridge.message over read-only TLS IMAP; no archive writes, dream or model calls','native_memcal_source_revision_verified':True,'harness_transport_losses':[],'native_length_limits_reached':0,'native_quote_removal_count':sum(c['quote_history_removed'] for c in checks),'native_url_normalization_count':sum(c['native_url_normalization'] for c in checks),'native_shortened_body_flags':sum(c['native_body_truncated_flag'] for c in checks),'email_checks':checks,'native_commands':commands,'model_calls':0,'native_store_modified':False,'conclusion':'Complete source transport and stock archive shaping verified. Shortening is native preprocessing, not harness truncation; extraction quality remains a benchmark outcome.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('component',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=audit(a.component);a.output.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps({k:r[k] for k in ['state','source_messages_verified','native_quote_removal_count','native_url_normalization_count','model_calls']}))

if __name__=='__main__':main()
