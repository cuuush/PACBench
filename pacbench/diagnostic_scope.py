"""An opt-in, fixed native MemCal diagnostic capability on the original ledger."""
import hashlib,json,os,re
from decimal import Decimal
ENV='PACBENCH_NATIVE_DIAGNOSTIC_SCOPE'
PROTOCOL='memcal-dac-native-diagnostic-v1'
REVISION='dac7214006b84ec364ecbcd9472daca7db588a25'
INPUT_MANIFEST_SHA256='fa5322ed6af511567ab3a46232a5b8c501cc3b40569a8b5bc339e4c4c8c0e665'
PROJECT='pacbench-'+hashlib.sha256((INPUT_MANIFEST_SHA256+REVISION).encode()).hexdigest()[:12]+'-memcal-memory'
MAX_REQUEST_USD=Decimal('0.27888400')
from .provider_versions import MEMCAL_OPERATIONS_REVISION
REVIEWED_PROTOCOLS={REVISION:PROTOCOL,
    MEMCAL_OPERATIONS_REVISION:'memcal-preference-operations-diagnostic-v1'}

def scope_for_revision(revision,manifest):
    if revision not in REVIEWED_PROTOCOLS or manifest!=INPUT_MANIFEST_SHA256:
        raise RuntimeError('Native diagnostic capability requires listed revision and frozen inputs')
    project='pacbench-'+hashlib.sha256((manifest+revision).encode()).hexdigest()[:12]+'-memcal-memory'
    return {'protocol':REVIEWED_PROTOCOLS[revision],'project':project,'source_revision':revision,
        'input_manifest_sha256':manifest,'max_attempts':12}

def selected(project,profile):
    value=os.environ.get(ENV)
    if not value:return None
    d=json.loads(value)
    if (not isinstance(d,dict) or set(d)!={'protocol','project','source_revision','input_manifest_sha256','max_attempts'}):
        raise RuntimeError('Native diagnostic capability/project/route differs')
    expected=scope_for_revision(d['source_revision'],d['input_manifest_sha256'])
    if (d!=expected or project!=d['project'] or profile!='luna-bedrock-global'):
        raise RuntimeError('Native diagnostic capability/project/route differs')
    return d

def admit(calls,scope,bound):
    if scope is None:return
    if bound>MAX_REQUEST_USD:raise RuntimeError('Native diagnostic request maximum exceeded')
    own=[r for r in calls if r.get('project')==scope['project']]
    if any(r.get('native_diagnostic_scope')!=scope for r in own):raise RuntimeError('Native diagnostic scope changed; preserve prior attempts')
    if any(r.get('state')!='settled' or r.get('error_label') or not r.get('billing_receipt') for r in own):
        raise RuntimeError('Native diagnostic prior attempt is unsettled or invalid; no replay or later call')
    stage=os.environ.get('PACBENCH_NATIVE_DIAGNOSTIC_STAGE_LIMIT')
    if stage is not None:
        if stage not in {'4','12'}:raise RuntimeError('Unknown native diagnostic stage ceiling')
        if len(own)>=int(stage):raise RuntimeError('Native diagnostic stage ceiling reached; await verified phase gate')
    if len(own)>=scope['max_attempts']:raise RuntimeError('Native diagnostic attempt ceiling reached; no replay')
