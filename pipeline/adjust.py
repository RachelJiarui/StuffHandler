from PIL import Image, ImageEnhance


def adjust(img: Image.Image, brightness: float, contrast: float, sharpness: float) -> Image.Image:
    if brightness == contrast == sharpness == 1.0:
        return img
    rgb = img.convert("RGB")
    if brightness != 1.0:
        rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
    if contrast != 1.0:
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast)
    if sharpness != 1.0:
        rgb = ImageEnhance.Sharpness(rgb).enhance(sharpness)
    return rgb
