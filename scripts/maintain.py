"""Check both staged source payloads and test the exported builder without publishing."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from departure_pixel_zh_builder.source import audit_index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--font-root", required=True, type=Path)
    args = parser.parse_args()
    builder = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix="departure-maintain-") as temporary:
        temporary = Path(temporary)
        builder_copy = temporary / "builder source"
        font_copy = temporary / "font source"
        reports = {
            "builder": audit_index(builder, builder_copy),
            "font": audit_index(args.font_root, font_copy),
        }
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(builder_copy / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=builder_copy,
            env=environment,
            check=True,
        )
        print(json.dumps({"source_checks": reports, "exported_builder_tests": "passed"}, indent=2))


if __name__ == "__main__":
    main()
