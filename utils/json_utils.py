"""Read static JSON assets; application state belongs to SQLite repositories."""

import json
from os import PathLike
from pathlib import Path

from config import ENCODING
from utils.json_types import JsonObject, is_json_object


def get_json(
    filename: str | PathLike[str], encoding: str = ENCODING
) -> JsonObject | None:
    """Read and validate one JSON object from disk.

    A missing file returns ``None``. Existing unreadable, malformed, or non-object
    files raise instead of being treated as empty state.

    Args:
        filename: File to read.
        encoding: Text encoding used to decode the file.

    Returns:
        The decoded JSON object, or ``None`` when the path does not exist.

    Raises:
        OSError: If an existing file cannot be read.
        json.JSONDecodeError: If its contents are not valid JSON.
        ValueError: If the top-level JSON value is not an object.
    """
    path = Path(filename)
    if not path.exists():
        return None
    with path.open(encoding=encoding) as data_file:
        payload: object = json.load(fp=data_file)  # pyright: ignore[reportAny]
    if not is_json_object(payload):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload
