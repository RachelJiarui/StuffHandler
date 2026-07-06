from PIL import Image
from rembg import remove


def remove_background(img: Image.Image, session) -> Image.Image:
    return remove(img, session=session)
