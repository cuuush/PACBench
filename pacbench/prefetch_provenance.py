"""Bind declared native automatic-retrieval conditions to actual injected text."""
import hashlib

VERSION = 'native-prefetch-provenance-v1'


def observe(declaration, text):
    if not declaration or not text:
        return None
    if (declaration.get('protocol') != VERSION or declaration.get('state') not in {'source','unverified'} or
            declaration.get('provider') not in {'cognee','memos'} or
            not isinstance(declaration.get('reason'),str) or not declaration['reason'].strip()):
        raise ValueError('Invalid native prefetch provenance condition')
    return {**declaration, 'prefetch_sha256':hashlib.sha256(text.encode()).hexdigest()}
