"""Argument-aware source restrictions for mixed native memory readers."""

SOURCE_ARGUMENT_RULES = {
    'viking_read': {
        'scheme': 'viking',
        'memory_namespaces': ['user','agent','~'],
        'memory_path': '/memories/',
        'summary_suffixes': {'abstract':'/.abstract.md','overview':'/.overview.md'},
        'default_level': 'overview',
    },
}


def proven_viking_summary(arguments, result):
    """Use the pinned native reader's receipt, which labels full-text fallback.

    An overview request alone is insufficient: files can fall back to content/read.
    Only complete successful receipts matching every requested URI establish that
    the answer saw generated summaries rather than the original source.
    """
    import json
    if (not isinstance(result, dict) or result.get('success') is not True or
            result.get('policy_blocked') or result.get('truncated')):
        return False
    level = arguments.get('level') or 'overview'
    if level not in {'abstract', 'overview'}:
        return False
    raw = arguments.get('uris') or arguments.get('uri')
    values = raw if isinstance(raw, list) else [raw]
    if not values or any(not isinstance(uri, str) or not uri.strip() for uri in values):
        return False
    uris = list(dict.fromkeys(uri.strip() for uri in values))
    try:
        payload = json.loads(result.get('text', ''))
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    if isinstance(raw, list):
        if (payload.get('level') != level or payload.get('requested') != len(uris) or
                payload.get('returned') != len(uris) or payload.get('truncated') is not False):
            return False
        rows = payload.get('results')
    else:
        rows = [payload]
    if not isinstance(rows, list) or len(rows) != len(uris):
        return False
    for uri, row in zip(uris, rows):
        resolved = uri
        for suffix in ('/.abstract.md', '/.overview.md', '/.read.md', '/.full.md'):
            if uri.endswith(suffix):
                resolved = uri[:-len(suffix)] or 'viking://'
                break
        if (not isinstance(row, dict) or row.get('uri') != uri or
                row.get('resolved_uri') != resolved or row.get('level') != level or
                'fallback' in row or 'error' in row or not isinstance(row.get('content'), str)):
            return False
    return True


def source_read_blocked(name, arguments, rules):
    """Fail closed on original reads; allow native memory and generated summaries.

    Summary pseudo-files are safe because the native reader normalizes them to a
    directory summary and cannot fall back to original file content. A plain file
    with overview requested is not safe: native readers can fall back to full text.
    """
    import posixpath
    from urllib.parse import unquote, urlsplit
    rule = rules.get(name)
    if rule is None:
        return False
    if not isinstance(arguments,dict):
        return True
    uri, uris = arguments.get('uri',''), arguments.get('uris',[])
    values = (uris if isinstance(uris,list) and uris else
              uri if isinstance(uri,list) else [uri])
    level = arguments.get('level') or rule['default_level']
    if not values or level not in ('abstract','overview','full'):
        return True
    for value in values:
        if not isinstance(value,str) or not value.strip():
            return True
        value = value.strip()
        try:
            parsed = urlsplit(value)
        except ValueError:
            return True
        if parsed.scheme != rule['scheme'] or parsed.query or parsed.fragment:
            return True
        path = posixpath.normpath('/'+unquote(parsed.netloc+parsed.path)).rstrip('/')+'/'
        memory = (parsed.netloc in rule['memory_namespaces'] and
                  rule['memory_path'] in path)
        suffix = rule['summary_suffixes'].get(level)
        summary = bool(suffix and value.endswith(suffix))
        if not memory and not summary:
            return True
    return False
