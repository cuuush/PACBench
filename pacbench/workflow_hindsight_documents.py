"""Native multipart document readiness for the ordered Hindsight Week input."""
from datetime import datetime, timezone
import hashlib
import time


class HindsightDocumentReadback:
    def __init__(self, client, bank, documents, *, all_documents=None):
        self.client = client
        self.bank = bank
        self.documents = tuple(documents)
        self.known_sources = {doc.filename for doc in (self.documents if all_documents is None else all_documents)}
        self.inputs = {}

    def wait(self, operation_ids, *, deadline):
        """Observe native file processing followed by bank-scoped job drain."""
        if len(set(operation_ids)) != len(operation_ids) or not operation_ids:
            raise ValueError('Hindsight did not identify its native file operations')
        terminal = {'failed','cancelled','canceled','not_found'}
        while time.monotonic() < deadline:
            statuses = [self.client.operations.get_operation_status(
                self.bank, operation, _request_timeout=15.0) for operation in operation_ids]
            if any(item.status in terminal for item in statuses):
                raise RuntimeError('Hindsight native file processing failed')
            if all(item.status=='completed' for item in statuses):
                return self.drain(deadline=deadline)
            time.sleep(.5)
        raise TimeoutError('Hindsight native file operation remained incomplete')

    def drain(self, *, deadline):
        while time.monotonic() < deadline:
            outstanding = {}
            for status in ('pending','processing','failed','cancelled'):
                page = self.client.operations.list_operations(
                    self.bank, status=status, limit=1, _request_timeout=15.0)
                outstanding[status] = page.total
            if outstanding['failed'] or outstanding['cancelled']:
                raise RuntimeError('Hindsight bank has failed or cancelled native operations')
            if not outstanding['pending'] and not outstanding['processing']:
                return outstanding
            time.sleep(.5)
        raise TimeoutError('Hindsight bank readiness timed out')

    def document(self, expected, operations, *, timeout=900):
        index = len(self.inputs)
        if index >= len(self.documents) or self.documents[index] != expected:
            raise ValueError('Hindsight file readback would change the frozen release order')
        drain = self.wait(operations, deadline=time.monotonic()+timeout)
        row = self.client.documents.get_document(self.bank, expected.filename, _request_timeout=15.0)
        if row.bank_id!=self.bank or row.id!=expected.filename or not isinstance(row.original_text,str):
            raise RuntimeError('Hindsight original file is missing from its native bank')
        genuine = hashlib.sha256(row.original_text.encode()).hexdigest()
        if genuine != expected.sha256:
            raise RuntimeError('Hindsight native uploaded text differs from the frozen source')
        self.inputs[row.id] = row.content_hash
        return dict(id=row.id, content_sha256=genuine, native_content_hash=row.content_hash,
                    memory_unit_count=row.memory_unit_count, native_operations=list(operations),
                    bank_jobs=drain, observed_at=datetime.now(timezone.utc).isoformat())

    def inventory(self):
        """Use one paginated native list, with exact hash checks after import."""
        documents = {}
        cursor = 0
        total = None
        while total is None or cursor < total:
            page = self.client.documents.list_documents(
                self.bank, limit=100, offset=cursor, _request_timeout=15.0)
            if total is None:
                total = page.total
            if (page.total_is_estimate or page.offset!=cursor or page.total!=total
                    or (cursor<total and not page.items)):
                raise RuntimeError('Hindsight document inventory changed or was incomplete')
            for row in page.items:
                if row.bank_id!=self.bank or row.id in documents:
                    raise RuntimeError('Hindsight document inventory crossed a bank or repeated an ID')
                documents[row.id] = row
            cursor += len(page.items)
        if cursor != total:
            raise RuntimeError('Hindsight document inventory listed a different total')
        observed = {key:row.content_hash for key,row in documents.items() if key in self.known_sources}
        if observed != self.inputs:
            raise RuntimeError('Hindsight native inventory differs from the verified source prefix')
        return list(self.inputs), documents
