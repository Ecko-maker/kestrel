"""Voice wake-up for the console: say "Kestrel", the display wakes, the falcon flies in, a fixed
greeting is spoken. Off by default (KESTREL_WAKE=on). See docs/voice-wake.md."""

from kestrel.wake.service import WakeService, WakeSettings


def build_wake_service(record_wake) -> WakeService | None:
    """The real service from the environment, or None when KESTREL_WAKE is off."""
    settings = WakeSettings.from_env()
    if not settings.enabled:
        return None
    from kestrel.wake.audio import MicSource
    from kestrel.wake.detector import RecognizerFactory, VoskDetector, vosk_recognizer_factory
    from kestrel.wake.power import default_power
    from kestrel.wake.speech import default_speaker
    from kestrel.wake.talk import CANCEL_WORD

    factories: list[RecognizerFactory] = []  # the model is loaded once, on first use, then reused

    def recognizers() -> RecognizerFactory:
        if not factories:
            factories.append(vosk_recognizer_factory(settings.model_dir))
        return factories[0]

    def make_detector() -> VoskDetector:
        return VoskDetector(recognizers(), settings.threshold)

    def make_cancel() -> VoskDetector:  # fresh for each countdown; any confidence: a false cancel is harmless
        return VoskDetector(recognizers(), 0.0, word=CANCEL_WORD, partial=True)

    make_transcriber = None
    if settings.talk:
        from kestrel.wake.transcribe import WhisperCppTranscriber, talk_model_file

        model_file = talk_model_file(settings.talk_model)
        if model_file.is_file():
            make_transcriber = lambda: WhisperCppTranscriber(model_file, prompt=settings.talk_prompt)  # noqa: E731
        else:
            print(f"[wake] spoken requests off: no talk model at {model_file} (scripts/get_talk_model.py)")
    return WakeService(
        settings,
        make_detector,
        MicSource,
        default_power(),
        default_speaker(),
        record_wake,
        make_transcriber=make_transcriber,
        make_cancel=make_cancel if make_transcriber is not None else None,
    )


__all__ = ["WakeService", "WakeSettings", "build_wake_service"]
