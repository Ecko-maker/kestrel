"""The microphone, as a stream of in-memory chunks. Nothing here writes, logs or sends audio:
each chunk goes from PortAudio's callback into a small bounded queue and on to the detector,
then it is dropped."""

import contextlib
import queue
from typing import Any, Protocol

from kestrel.wake.detector import SAMPLE_RATE

CHUNK_FRAMES = 4_000  # 0.25 s at 16 kHz
MAX_QUEUED = 40  # ~10 s; older audio is dropped rather than piling up


class AudioSource(Protocol):
    def open(self) -> None: ...
    def read(self, timeout: float) -> bytes | None:
        """The next chunk, or None if none arrived within timeout seconds."""
        ...

    def close(self) -> None: ...


class MicSource:
    def __init__(self) -> None:
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=MAX_QUEUED)
        self._stream: Any = None

    def _callback(self, indata, frames, time, status) -> None:  # PortAudio's audio thread
        with contextlib.suppress(queue.Full):  # the detector fell behind: drop this chunk
            self._queue.put_nowait(bytes(indata))

    def open(self) -> None:
        import sounddevice  # optional dependency: uv sync --extra wake

        self._stream = sounddevice.RawInputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=CHUNK_FRAMES, callback=self._callback
        )
        self._stream.start()

    def read(self, timeout: float) -> bytes | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        while not self._queue.empty():  # forget anything not yet processed
            self._queue.get_nowait()
