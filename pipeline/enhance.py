import base64
import time
from io import BytesIO

from openai import OpenAI, RateLimitError
from PIL import Image

DEFAULT_PROMPT = (
    "Professional photo-realistic product photo of this clothing item. "
    "Remove the clothing hanger and wall hook."
    "Preserve all fabric textures, patterns, and construction details. "
    "Fix uneven draping and remove harsh shadows. "
    "Slightly emphasize defining details — buttons, stitching, texture patterns — so they read clearly at a glance. "
    "Preserve exact sleeve length; If the sleeves do not pass the end of the shirt, it must be shorter sleeves. Both sleeves must be symmetrical. "
    "Preserve all lettering and designs 100%."
    "Background: solid white or black or hot pink, whichever contrasts best with the garment. "
    "Lighting: bright, clean, and even studio light."
)


def enhance_image(
    client: OpenAI, img: Image.Image, prompt: str, retries: int = 3
) -> Image.Image:
    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG")

    for attempt in range(retries):
        buf.seek(0)
        try:
            response = client.images.edit(
                model="gpt-image-2",
                image=("image.png", buf, "image/png"),
                prompt=prompt,
            )
            b64 = response.data[0].b64_json
            return Image.open(BytesIO(base64.b64decode(b64)))
        except RateLimitError:
            if attempt < retries - 1:
                time.sleep(5 * (2**attempt))  # 5s, 10s
            else:
                raise
