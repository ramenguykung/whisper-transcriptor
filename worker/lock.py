"""Resolve a platform-specific wheel lock without installing dependencies."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    source = Path(__file__).with_name("requirements.in")
    with tempfile.TemporaryDirectory() as directory:
        report_path = Path(directory) / "report.json"
        subprocess.run([sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed",
                        "--only-binary=:all:", "--quiet", "--report", str(report_path), "-r", str(source)], check=True)
        report = json.loads(report_path.read_text())
    print("# Linux x86_64 wheel lock. Regenerate with worker/lock.py in the matching Python image.")
    print(f"# Python {sys.version.split()[0]}")
    for item in sorted(report["install"], key=lambda item: item["metadata"]["name"].lower()):
        metadata = item["metadata"]
        sha256 = item["download_info"]["archive_info"]["hashes"]["sha256"]
        print(f"{metadata['name']}=={metadata['version']} --hash=sha256:{sha256}")


if __name__ == "__main__":
    main()
