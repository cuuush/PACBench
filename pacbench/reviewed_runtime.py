"""One explicit frozen-review compatibility proof for an audit-only correction.

Do not rewrite the frozen audit or broadly accept source drift. These exact
file hashes differ only inside audit_followups; its declaration and every
generation, review, eligibility, scoring and runtime statement remain bound.
"""
import ast
import hashlib
from pathlib import Path

REVIEWED_SHA = 'da11d4f51d9a6bfad6839088cf6f20e3645ba13bf92663f62301db6f5878875c'
CURRENT_SHA = '04f71e3f09a76ea4f499ac58cb6076a7b9aa74870202601c714c346e8ac28622'
RUNTIME_AST_SHA = '7be269f60c64e4f2955f695ebe8f72689025f3a7089e7b5aca48d7b8466bfee0'


def execution_digest(source):
    tree=ast.parse(source)
    for node in tree.body:
        if isinstance(node,ast.FunctionDef) and node.name=='audit_followups':node.body=[ast.Pass()]
    return hashlib.sha256(ast.dump(tree,include_attributes=False).encode()).hexdigest()


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
