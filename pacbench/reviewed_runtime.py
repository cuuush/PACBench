"""One explicit frozen-review compatibility proof for an audit-only correction.

Do not rewrite the frozen audit or broadly accept source drift. These exact
file hashes differ only inside audit_followups; its declaration and every
generation, review, eligibility, scoring and runtime statement remain bound.
"""
import ast
import hashlib
import json
from pathlib import Path

REVIEWED_SHA = 'da11d4f51d9a6bfad6839088cf6f20e3645ba13bf92663f62301db6f5878875c'
CURRENT_SHA = '04f71e3f09a76ea4f499ac58cb6076a7b9aa74870202601c714c346e8ac28622'
RUNTIME_AST_SHA = 'a902b7ab0d91e76e5ee59e47d214ede346a056b5cafe459db59895896d08d587'


def execution_digest(source):
    tree=ast.parse(source)
    for node in tree.body:
        if isinstance(node,ast.FunctionDef) and node.name=='audit_followups':node.body=[ast.Pass()]
    def canonical(value):
        if isinstance(value,ast.AST):
            return {'_type':type(value).__name__,**{k:canonical(v) for k,v in ast.iter_fields(value) if v is not None and v!=[]}}
        if isinstance(value,list):return [canonical(v) for v in value]
        return value
    # Python 3.14's ast.dump omits empty fields by default; canonicalize them
    # explicitly so the same parsed program has the same proof on 3.12/3.14.
    return hashlib.sha256(json.dumps(canonical(tree),sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def audit_only_compatibility(repo,path,reviewed_sha):
    repo,path=Path(repo).resolve(),Path(path).resolve()
    source=path.read_bytes();current_sha=hashlib.sha256(source).hexdigest()
    if (path!=repo/'pacbench/followups.py' or reviewed_sha!=REVIEWED_SHA or current_sha!=CURRENT_SHA or
            execution_digest(source.decode())!=RUNTIME_AST_SHA):
        raise ValueError('Frozen reviewed source differs outside the exact audit-only compatibility pair')
    return {'protocol':'reviewed-followup-audit-only-compatibility-v1','path':'pacbench/followups.py',
        'reviewed_sha256':reviewed_sha,'current_sha256':current_sha,'execution_ast_sha256':RUNTIME_AST_SHA,
        'reviewed_source_revision':'486d7e1faefe01ef4f3cc344934ce85407b5a91c',
        'changed_body_only':'audit_followups','frozen_review_rewritten':False,
        'scope':'Post-run refusal audit only; generation, judging, eligibility, scoring and prompts unchanged'}
