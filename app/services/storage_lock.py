from functools import wraps
from threading import RLock


_document_storage_lock = RLock()


def document_storage_serialized(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        # ponytail: the portable server is one process; use an OS file lock if that changes.
        with _document_storage_lock:
            return func(*args, **kwargs)

    return wrapped
