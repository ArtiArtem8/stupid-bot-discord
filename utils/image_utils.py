import random
from pathlib import Path

import requests


def save_image(image_url: str, save_to: str | Path) -> str:
    """Downloads an image from the provided URL and saves it locally.
    Returns the filename used."""
    img_data = requests.get(image_url).content
    name = str(random.randint(2**27, 2**28))
    filename = Path(name).with_suffix(".png")
    with open(save_to / filename, "wb") as handler:
        handler.write(img_data)
    return filename
