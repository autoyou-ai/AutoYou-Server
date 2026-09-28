import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest


def test_browser_archive_contains_links_and_preserves_normal_frameworks(tmp_path):
    root = Path(__file__).resolve().parents[3] / "node" / "whatsapp"
    node = shutil.which("node")
    if not node or not (root / "node_modules/@puppeteer/browsers").is_dir():
        pytest.skip("Install node/whatsapp dependencies for the browser archive check")

    def archive(name, entries):
        path = tmp_path / (name + ".zip")
        with zipfile.ZipFile(path, "w") as zipped:
            for filename, content, link in entries:
                entry = zipfile.ZipInfo(filename)
                entry.create_system = 3
                entry.external_attr = ((stat.S_IFLNK if link else stat.S_IFREG) | 0o755) << 16
                zipped.writestr(entry, content)
        return path

    def extract(source, destination):
        return subprocess.run([node, "-e", """
            const {createRequire} = require('node:module');
            const fromBrowser = createRequire(require.resolve('@puppeteer/browsers'));
            const extract = fromBrowser('extract-zip');
            extract(process.argv[1], {dir: process.argv[2]}).catch(error => {
                console.error(error.message); process.exitCode = 1;
            });
        """, str(source), str(destination)], cwd=root, capture_output=True, text=True, timeout=10)

    outside = tmp_path / "outside.txt"
    outside.write_text("synthetic outside marker")
    try:
        (tmp_path / "link-permission-probe").symlink_to(outside)
        can_symlink = True
    except OSError:
        can_symlink = False
    cases = {
        "relative-link": [("link", "../outside.txt", True)],
        "absolute-link": [("link", str(outside), True)],
        "chain": [("a", "b", True), ("b", "../outside.txt", True)],
        "traversal": [("../outside.txt", "overwrite", False)],
        "existing-file-link": [("file", "overwrite", False)],
        "existing-parent-link": [("parent/new/file", "overwrite", False)],
    }
    for name, entries in cases.items():
        if name.startswith("existing-") and not can_symlink:
            continue
        destination = tmp_path / name
        destination.mkdir()
        if name == "existing-file-link":
            (destination / "file").symlink_to(outside)
        if name == "existing-parent-link":
            (destination / "parent").symlink_to(tmp_path, target_is_directory=True)
        result = extract(archive(name, entries), destination)
        assert result.returncode != 0, name
        assert not (destination / "link").is_symlink(), name
        assert outside.read_text() == "synthetic outside marker", name
        assert not (tmp_path / "new").exists(), name

    entries = [("Versions/A/browser", "synthetic browser", False)]
    if can_symlink:
        entries.append(("Versions/Current", "A", True))
    valid = archive("valid", entries)
    destination = tmp_path / "valid"
    result = extract(valid, destination)
    assert result.returncode == 0, result.stderr
    browser = "Versions/Current/browser" if can_symlink else "Versions/A/browser"
    assert (destination / browser).read_text() == "synthetic browser"
