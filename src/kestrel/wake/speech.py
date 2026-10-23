"""Speaking the greeting with Windows' own speech engine (SAPI 5), through comtypes.

SAPI's desktop voices (e.g. Microsoft David, Zira) run entirely on this PC: no text leaves it.
Speech runs on its own thread, which owns the COM object, so a wake never waits for it.
"""

import queue
import sys
import threading
from typing import Protocol


class Speaker(Protocol):
    def say(self, text: str) -> None: ...
    def close(self) -> None: ...


class NullSpeaker:
    def say(self, text: str) -> None:
        pass

    def close(self) -> None:
        pass


class SapiSpeaker:
    def __init__(self) -> None:
        self._texts: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="kestrel-speech", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        import comtypes  # optional dependency: uv sync --extra wake
        import comtypes.client

        comtypes.CoInitialize()
        try:
            voice = comtypes.client.CreateObject("SAPI.SpVoice")
            while (text := self._texts.get()) is not None:
                voice.Speak(text, 0)  # 0 = synchronous on this thread
        except Exception as e:  # speech is a nicety; never take the server down
            print(f"[wake] speech unavailable: {type(e).__name__}: {e}", file=sys.stderr)
        finally:
            comtypes.CoUninitialize()

    def say(self, text: str) -> None:
        self._texts.put(text)

    def close(self) -> None:
        self._texts.put(None)


def default_speaker() -> Speaker:
    return SapiSpeaker() if sys.platform == "win32" else NullSpeaker()
