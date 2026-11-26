"""Read exact artifact hashes and publish completed private artifacts once."""
import hashlib
import os
import tempfile


def file_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()



def new_private_file(destination, chunks):
    """Publish a complete private local artifact once, even with competing writers."""
    with tempfile.NamedTemporaryFile(dir=destination.parent) as tmp:
        for chunk in chunks:
            tmp.write(chunk)
        tmp.flush()
        os.fsync(tmp.fileno())
        os.link(tmp.name, destination)
