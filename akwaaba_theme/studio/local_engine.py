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


# ---------------------------------------------------------------------------
# Jobs: hand the work to the image Mac and collect it later.
#
# The image Mac keeps a job on disk and carries on without us, so the laptop (or the
# network between them) may go away. Nothing here waits for a drawing to finish.
# ---------------------------------------------------------------------------

class Unreachable(GeminiError):
	"""The image Mac couldn't be reached. The work isn't lost: ask again later."""


def _call(method, url, path, *, json=None, timeout=30):
	try:
		return requests.request(method, url.rstrip("/") + path, json=json, timeout=timeout)
	except requests.RequestException as e:
		raise Unreachable(f"Couldn't reach the local image tool at {url}: {e.__class__.__name__}")


def submit_job(*, url, prompt, image_bytes, aspect_ratio="3:4", image_size="1K", seed=None, timeout=60):
	"""Queue a drawing on the image Mac and return its job id. The same request again returns
	the same job, so a retry after a dropped connection doesn't draw twice."""
	width, height = dimensions(aspect_ratio, image_size)
	body = {
		"prompt": prompt,
		"init_images": [base64.b64encode(image_bytes).decode("ascii")],
		"width": width,
		"height": height,
		"batch_size": 1,
	}
	if seed is not None:
		body["seed"] = seed
	resp = _call("POST", url, "/jobs", json=body, timeout=timeout)
	if resp.status_code != 202:
		raise GeminiError(f"The local image tool answered {resp.status_code}: {resp.text[:200]}")
	try:
		return resp.json()["job_id"]
	except (ValueError, KeyError):
		raise GeminiError("The local image tool didn't accept the job.")


def job_status(*, url, job_id):
	"""{"status": queued|running|done|failed|missing, "error": …, "progress": …}"""
	resp = _call("GET", url, f"/jobs/{job_id}", timeout=10)
	if resp.status_code == 404:
		return {"status": "missing", "error": "The image Mac no longer has this job."}
	if resp.status_code != 200:
		raise GeminiError(f"The local image tool answered {resp.status_code}: {resp.text[:200]}")
	return resp.json()


def job_result(*, url, job_id):
	"""JPEG bytes of a finished job. Raises GeminiError."""
	resp = _call("GET", url, f"/jobs/{job_id}/result", timeout=120)
	if resp.status_code != 200:
		raise GeminiError(f"The local image tool answered {resp.status_code}: {resp.text[:200]}")
	try:
		img = Image.open(io.BytesIO(base64.b64decode(resp.json()["images"][0])))
		buf = io.BytesIO()
		img.convert("RGB").save(buf, "JPEG", quality=92)
		return buf.getvalue()
	except (ValueError, KeyError, IndexError, OSError):
		raise GeminiError("The local image tool didn't return an image.")


def acknowledge_job(*, url, job_id):
	"""Tell the image Mac the draft is safe here, so it can delete the job's photo and result."""
	try:
		_call("DELETE", url, f"/jobs/{job_id}")
	except Unreachable:
		pass  # it keeps the draft for a month; nothing is lost
