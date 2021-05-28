from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict  # noqa


class PakerImportError(Exception):

    def __init__(self, message, details=None):
        # type: (str, Dict[str, Any]) -> None
        super().__init__(message)
        self.details = details or {}
