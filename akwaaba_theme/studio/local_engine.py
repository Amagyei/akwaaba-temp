"""An image tool running on this machine, instead of Gemini.

Speaks the img2img call that Draw Things and other Stable Diffusion tools share.
Same shape as gemini.edit_image, and it touches no frappe state either, so
callers may run it in worker threads. Costs nothing per image, but test how exactly
a local model keeps the garment before relying on it.
"""

import base64
import io

import requests
from PIL import Image

from akwaaba_theme.studio.gemini import GeminiError

LONG_SIDE = {"1K": 1024, "2K": 2048, "4K": 4096}


def dimensions(aspect_ratio, image_size):
	w, h = (int(n) for n in (aspect_ratio or "3:4").split(":"))
	long_side = LONG_SIDE.get(image_size) or 1024
	scale = long_side / max(w, h)
	# These tools want multiples of 64.
	return round(w * scale / 64) * 64, round(h * scale / 64) * 64


def edit_image(*, url, prompt, image_bytes, aspect_ratio="3:4", image_size="1K", timeout=300):
	"""Return JPEG bytes for `prompt` applied to `image_bytes`. Raises GeminiError."""
	width, height = dimensions(aspect_ratio, image_size)
	try:
		resp = requests.post(
			url.rstrip("/") + "/sdapi/v1/img2img",
			json={
				"prompt": prompt,
				"init_images": [base64.b64encode(image_bytes).decode("ascii")],
				"width": width,
				"height": height,
				"batch_size": 1,
			},
			timeout=timeout,
		)
	except requests.Timeout:
		raise GeminiError(f"The local image tool took longer than {timeout}s.")
	except requests.RequestException as e:
		raise GeminiError(f"Couldn't reach the local image tool at {url}: {e.__class__.__name__}")
	if resp.status_code != 200:
		raise GeminiError(f"The local image tool answered {resp.status_code}: {resp.text[:200]}")
	try:
		images = resp.json().get("images") or []
		img = Image.open(io.BytesIO(base64.b64decode(images[0])))
		buf = io.BytesIO()
		img.convert("RGB").save(buf, "JPEG", quality=92)
		return buf.getvalue()
	except (ValueError, IndexError, OSError):
		raise GeminiError("The local image tool didn't return an image.")
