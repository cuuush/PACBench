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
