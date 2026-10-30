"""Speaking with Windows' own speech engine (SAPI 5), through comtypes.

SAPI's desktop voices (e.g. Microsoft David, Zira) run entirely on this PC: no text leaves it.
Speech runs on its own thread, which owns the COM object. Speaking is asynchronous so it can be
stopped at once (barge-in: saying "Kestrel" while it speaks), and busy() tells the recorder not to
record Kestrel's own voice as your request.
"""

import queue
import sys
import threading
from typing import Protocol

SVSF_ASYNC = 1
SVSF_PURGE_BEFORE_SPEAK = 2


class Speaker(Protocol):
    def say(self, text: str) -> None: ...
    def stop(self) -> None: ...
    def busy(self) -> bool: ...
    def close(self) -> None: ...


class NullSpeaker:
    def say(self, text: str) -> None:
        pass

    def stop(self) -> None:
        pass

    def busy(self) -> bool:
        return False

    def close(self) -> None:
        pass


class SapiSpeaker:
    def __init__(self) -> None:
        self._commands: queue.Queue[tuple[str, str] | None] = queue.Queue()
        self._busy = threading.Event()
        self._thread = threading.Thread(target=self._run, name="kestrel-speech", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        import comtypes  # optional dependency: uv sync --extra wake
        import comtypes.client

        comtypes.CoInitialize()
        try:
            voice = comtypes.client.CreateObject("SAPI.SpVoice")
            while True:
                try:
                    command = self._commands.get(timeout=0.05)
                except queue.Empty:
                    command = ("poll", "")
                if command is None:
                    break
                kind, text = command
                if kind == "say":
                    voice.Speak(text, SVSF_ASYNC)
                elif kind == "stop":
                    voice.Speak("", SVSF_ASYNC | SVSF_PURGE_BEFORE_SPEAK)  # drops what is playing and queued
                # Not "is speaking": SAPI also has queued (0, ~1 s before the audio starts) and stopping
                # (3) states. Done means done: WaitUntilDone(0) is True only when nothing is left to say.
                if not voice.WaitUntilDone(0):
                    self._busy.set()
                elif self._commands.empty():
                    self._busy.clear()
        except Exception as e:  # speech is a nicety; never take the server down
            print(f"[wake] speech unavailable: {type(e).__name__}: {e}", file=sys.stderr)
            self._busy.clear()
        finally:
            comtypes.CoUninitialize()

    def say(self, text: str) -> None:
        self._busy.set()  # at once, so the recorder never takes the start of it for your voice
        self._commands.put(("say", text))

    def stop(self) -> None:
        self._commands.put(("stop", ""))

    def busy(self) -> bool:
        return self._busy.is_set()

    def close(self) -> None:
        self._commands.put(None)


def default_speaker() -> Speaker:
    return SapiSpeaker() if sys.platform == "win32" else NullSpeaker()
