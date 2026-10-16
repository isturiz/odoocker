"""Short, fail-fast locks; do not wait indefinitely behind another agent."""

import fcntl
from contextlib import contextmanager

from .errors import OdxError


@contextmanager
def locked(path, *, shared=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        try:
            fcntl.flock(stream.fileno(), operation | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OdxError(
                f"Resource busy ({path.name}); retry after the active operation"
            ) from error
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
