"""The two `.env` lines the Telegram channel needs, checked before anything connects.

Startup refuses to run without both, and never falls back to "anyone may talk to the bot". Errors
name the line to add but never echo the token.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

TOKEN_VAR = "TELEGRAM_BOT_TOKEN"
USER_VAR = "TELEGRAM_ALLOWED_USER_ID"
TOKEN_SHAPE = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}")  # what @BotFather issues (also in tracing.redact)

ENV_LINES = (
    f"{TOKEN_VAR}=<the token @BotFather gives you for your bot>\n"
    f"{USER_VAR}=<your numeric Telegram user id, digits only>"
)


class TelegramConfigError(RuntimeError):
    """A missing or malformed setting. The message says which .env line to add or fix."""


@dataclass(frozen=True)
class TelegramSettings:
    token: str = field(repr=False)  # never printed, not even in a repr
    allowed_user_id: int


def load_settings(env: Mapping[str, str] | None = None) -> TelegramSettings:
    env = os.environ if env is None else env
    token = (env.get(TOKEN_VAR) or "").strip()
    user = (env.get(USER_VAR) or "").strip()
    missing = [name for name, value in ((TOKEN_VAR, token), (USER_VAR, user)) if not value]
    if missing:
        raise TelegramConfigError(
            f"Telegram isn't configured: {' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set. "
            f"Add these lines to .env (docs/telegram.md says how to get both):\n{ENV_LINES}"
        )
    if not TOKEN_SHAPE.fullmatch(token):
        raise TelegramConfigError(
            f"{TOKEN_VAR} doesn't look like a bot token (digits, a colon, then 30+ letters, digits, _ or -). "
            "Copy it again from @BotFather."
        )
    if not user.isascii() or not user.isdigit() or int(user) <= 0:
        raise TelegramConfigError(
            f"{USER_VAR} must be your numeric Telegram user id (digits only), not a @username. "
            "docs/telegram.md says how to find it."
        )
    return TelegramSettings(token, int(user))
