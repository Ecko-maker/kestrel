"""Fetch the wake word's model once: Vosk's small English model (Apache-2.0, 41 MB zip, 68 MB unpacked).

It goes outside the repo (default %LOCALAPPDATA%\\kestrel\\models, or KESTREL_WAKE_MODEL's folder),
and the zip must match the pinned SHA-256 before anything is unpacked. This script is the only wake
code that touches the network, and only when you run it.

    uv run --no-sync python scripts/get_wake_model.py                 # download, check, unpack
    uv run --no-sync python scripts/get_wake_model.py --zip model.zip # use a zip you already have
"""

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from kestrel.wake.service import MODEL_NAME, default_model_dir

URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
SHA256 = "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498"  # 41,205,931 bytes, 2026-10-22


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def unpack(archive: Path, dest: Path) -> Path:
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():  # no member may land outside dest
            if not (dest / name).resolve().is_relative_to(dest.resolve()):
                raise ValueError(f"unsafe path in the zip: {name}")
        z.extractall(dest)
    return dest / MODEL_NAME


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zip", type=Path, help="a copy of the model zip you already downloaded")
    env = os.getenv("KESTREL_WAKE_MODEL")
    parser.add_argument("--dest", type=Path, default=(Path(env) if env else default_model_dir()).parent)
    args = parser.parse_args()

    target = args.dest / MODEL_NAME
    if (target / "am" / "final.mdl").is_file():
        print(f"already there: {target}")
        return 0
    args.dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.dest) as tmp:
        archive = Path(tmp) / f"{MODEL_NAME}.zip"
        if args.zip:
            shutil.copyfile(args.zip, archive)
        else:
            print(f"downloading {URL} (41 MB)")
            urllib.request.urlretrieve(URL, archive)
        found = sha256(archive)
        if found != SHA256:
            print(f"SHA-256 mismatch: got {found}, expected {SHA256}. Nothing unpacked.", file=sys.stderr)
            return 1
        print(f"SHA-256 ok ({archive.stat().st_size:,} bytes)")
        print(f"unpacked to {unpack(archive, args.dest)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
