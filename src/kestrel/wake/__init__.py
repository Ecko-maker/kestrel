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

    return WakeService(settings, make_detector, MicSource, default_power(), default_speaker(), record_wake)


__all__ = ["WakeService", "WakeSettings", "build_wake_service"]
