"""Exercise the actual PDF installation shell block without Docker or networking."""

import json
import os
import subprocess

import pytest

from odoocker.runtime import PACKAGE

HASHES = {
    "amd64": "98ba0d157b50d36f23bd0dedf4c0aa28c7b0c50fcdcdc54aa5b6bbba81a3941d",
    "arm64": "b6606157b27c13e044d0abbe670301f88de4e1782afca4f9c06a5817f3e03a9c",
}


@pytest.fixture
def install_pdf(tmp_path):
    dockerfile = (PACKAGE / "runtime.Dockerfile").read_text()
    # Execute the shipped block, not a second implementation of the installer.
    blocks = dockerfile.replace("\\\n", "").splitlines()
    (block,) = [line.removeprefix("RUN ") for line in blocks if line.startswith("RUN set -eu;")]
    assert "wkhtmltox" in block
    assert dockerfile.index("RUN set -eu;") < dockerfile.index("USER odoo")
    commands = tmp_path / "bin"
    commands.mkdir()
    stub = commands / "stub"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "name = pathlib.Path(sys.argv[0]).name\n"
        "entry = {'command': name, 'args': sys.argv[1:]}\n"
        "if name == 'sha256sum': entry['stdin'] = sys.stdin.read()\n"
        "with open(os.environ['PDF_LOG'], 'a') as log:\n"
        "    log.write(json.dumps(entry) + '\\n')\n"
        "if os.environ.get('PDF_FAIL') in (name, name + ':' + sys.argv[1]): sys.exit(1)\n"
        "if name == 'dpkg': print(os.environ['PDF_ARCH'])\n"
        "if name == 'wkhtmltopdf': print(os.environ['PDF_VERSION'])\n"
    )
    stub.chmod(0o755)
    for command in ("dpkg", "curl", "sha256sum", "apt-get", "wkhtmltopdf", "rm"):
        (commands / command).symlink_to(stub)
    log = tmp_path / "calls.jsonl"

    def run(arch="arm64", *, fail="", version="wkhtmltopdf 0.12.6.1 (with patched qt)"):
        result = subprocess.run(
            ["sh", "-c", block],
            env={
                **os.environ,
                "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
                "PDF_LOG": str(log),
                "PDF_ARCH": arch,
                "PDF_FAIL": fail,
                "PDF_VERSION": version,
            },
            capture_output=True,
            text=True,
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        return result, calls

    return run


@pytest.mark.parametrize("arch", HASHES)
def test_pdf_install_is_default_pinned_and_verified_before_install(install_pdf, arch):
    result, calls = install_pdf(arch)
    assert result.returncode == 0, result.stderr
    assert [call["command"] for call in calls] == [
        "dpkg",
        "curl",
        "sha256sum",
        "apt-get",
        "apt-get",
        "wkhtmltopdf",
        "rm",
        "rm",
    ]
    assert calls[0]["args"] == ["--print-architecture"]
    assert calls[1]["args"] == [
        "-fsSL",
        "--retry",
        "3",
        "https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/"
        f"wkhtmltox_0.12.6.1-3.bookworm_{arch}.deb",
        "-o",
        "/tmp/wkhtmltox.deb",
    ]
    assert calls[2] == {
        "command": "sha256sum",
        "args": ["-c", "-"],
        "stdin": f"{HASHES[arch]}  /tmp/wkhtmltox.deb\n",
    }
    assert calls[3]["args"] == ["update"]
    assert calls[4]["args"] == [
        "install",
        "-y",
        "--no-install-recommends",
        "/tmp/wkhtmltox.deb",
    ]
    assert calls[5]["args"] == ["--version"]
    assert calls[6]["args"] == ["-f", "/tmp/wkhtmltox.deb"]
    assert calls[7]["args"][0] == "-rf"
    assert all(path.startswith("/var/lib/apt/lists/") for path in calls[7]["args"][1:])


@pytest.mark.parametrize("arch", ["ppc64el", "armhf", "unknown", ""])
def test_pdf_install_rejects_unsupported_arch_before_download(install_pdf, arch):
    result, calls = install_pdf(arch)
    assert result.returncode != 0
    assert f"Unsupported wkhtmltopdf architecture: {arch}" in result.stderr
    assert [call["command"] for call in calls] == ["dpkg"]


@pytest.mark.parametrize(
    "fail", ["dpkg", "curl", "sha256sum", "apt-get", "apt-get:install", "wkhtmltopdf"]
)
def test_pdf_install_fails_closed(install_pdf, fail):
    result, calls = install_pdf(fail=fail)
    assert result.returncode != 0
    assert calls[-1]["command"] == fail.partition(":")[0]
    if fail == "apt-get:install":
        assert calls[-1]["args"][0] == "install"
    if fail in {"dpkg", "curl", "sha256sum"}:
        assert not any(call["command"] == "apt-get" for call in calls)


@pytest.mark.parametrize(
    "version", ["wkhtmltopdf 0.12.6.1", "wkhtmltopdf 0.12.5 (with patched qt)"]
)
def test_pdf_install_rejects_unpatched_or_wrong_version(install_pdf, version):
    result, calls = install_pdf(version=version)
    assert result.returncode != 0
    assert calls[-1]["command"] == "wkhtmltopdf"
