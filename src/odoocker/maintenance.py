"""Linux maintenance leases shared by setup and backed-up OpenCode updates."""

import json
import select
import subprocess
from contextlib import contextmanager

from .errors import OdxError


class Maintenance:
    def __init__(self, process, protocol="ODX_UPDATE"):
        self.process = process
        self.protocol = protocol
        self.stopped = False

    def expect(self, status, timeout=60):
        if not select.select([self.process.stdout], [], [], timeout)[0]:
            raise OdxError("Timed out waiting for the maintenance container")
        line = self.process.stdout.readline()
        if line.strip() != self.protocol + " " + json.dumps({"status": status}):
            raise OdxError("Maintenance lock/backup failed; an Odoo operation may be active")

    def backup(self):
        self.process.stdin.write("backup\n")
        self.process.stdin.flush()
        self.expect("backed_up", timeout=300)


@contextmanager
def maintenance_process(args, *, env, protocol="ODX_UPDATE"):
    process = subprocess.Popen(
        args,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        guard = Maintenance(process, protocol)
        guard.expect("locked")
        yield guard
    finally:
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()
