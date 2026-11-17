"""Authenticated upstream requests must not forward secrets on redirects."""
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_without_redirect(request, *, timeout):
    # build_opener retains urllib's normal proxy and HTTPS certificate handling.
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)
