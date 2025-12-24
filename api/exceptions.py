class StupidBotError(Exception):
    """Base exception for all bot-related errors."""

    def __init__(self, message: str, user_message: str | None = None) -> None:
        super().__init__(message)
        self.user_message = user_message or message


class BusinessError(StupidBotError):
    """Exception for business logic violations (e.g. invalid operation)."""

    pass


class InfrastructureError(StupidBotError):
    """Exception for infrastructure failures (e.g. database down)."""

    pass


class DataAccessError(InfrastructureError):
    """Exception for data retrieval/persistence errors."""

    pass


# Music Specific
class MusicError(StupidBotError):
    """Base exception for music module errors."""

    pass


class VoiceConnectionError(MusicError):
    """Error connecting to voice."""

    pass
