"""Fetch the speech-to-text model for spoken requests once: whisper.cpp base.en, 5-bit quantised
(MIT, 57 MB), or the faster tiny.en (31 MB). Outside the repo (default %LOCALAPPDATA%\\kestrel\\models\\
whisper), and the file must match the SHA-256 pinned in kestrel.wake.transcribe.TALK_MODELS before it
is kept. This script is the only talk code that uses the network, and only when you run it.

    uv run --no-sync python scripts/get_talk_model.py                       # base.en-q5_1
    uv run --no-sync python scripts/get_talk_model.py --model tiny.en-q5_1  # the fallback
    uv run --no-sync python scripts/get_talk_model.py --file ggml-base.en-q5_1.bin  # a copy you have
"""

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

from kestrel.wake.transcribe import DEFAULT_TALK_MODEL, TALK_MODELS, default_talk_dir

URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{name}.bin"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=DEFAULT_TALK_MODEL, choices=sorted(TALK_MODELS))
    parser.add_argument("--file", type=Path, help="a copy of the model file you already downloaded")
    parser.add_argument("--dest", type=Path, default=default_talk_dir())
    args = parser.parse_args()

    expected, size = TALK_MODELS[args.model]
    target = args.dest / f"ggml-{args.model}.bin"
    if target.is_file() and sha256(target) == expected:
        print(f"already there: {target}")
        return 0
    args.dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.dest) as tmp:
        download = Path(tmp) / target.name
        if args.file:
            shutil.copyfile(args.file, download)
        else:
            print(f"downloading {URL.format(name=args.model)} ({size / 1e6:.0f} MB)")
            urllib.request.urlretrieve(URL.format(name=args.model), download)
        found = sha256(download)
        if found != expected:
            print(f"SHA-256 mismatch: got {found}, expected {expected}. Nothing kept.", file=sys.stderr)
            return 1
        shutil.move(download, target)
    print(f"SHA-256 ok: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
