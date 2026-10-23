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

    factories: list[RecognizerFactory] = []  # the model is loaded once, on first use, then reused

    def make_detector() -> VoskDetector:
        if not factories:
            factories.append(vosk_recognizer_factory(settings.model_dir))
        return VoskDetector(factories[0], settings.threshold)

    make_transcriber = None
    if settings.talk:
        from kestrel.wake.transcribe import WhisperCppTranscriber, talk_model_file

        model_file = talk_model_file(settings.talk_model)
        if model_file.is_file():
            make_transcriber = lambda: WhisperCppTranscriber(model_file)  # noqa: E731 (loaded on first request)
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
    )


__all__ = ["WakeService", "WakeSettings", "build_wake_service"]
