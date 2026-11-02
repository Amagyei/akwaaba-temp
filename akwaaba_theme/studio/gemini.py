"""Gemini image editing over the Interactions API.

One call = one garment photo + one prompt → one generated JPEG.

Uses plain `requests` and touches no frappe state, so callers may run several
of these in parallel threads.

Contract (ai.google.dev/api/interactions, 2026-10):
	POST /v1beta/interactions
	{"model", "input": [text block, image block], "response_format": {...}}
	→ {"status", "errors": [...], "steps": [{"type": "model_output",
	   "content": [{"type": "image", "data": <base64>, "mime_type"}]}]}
"""

import base64

import requests

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"

# USD per output image, from ai.google.dev/gemini-api/docs/pricing (updated
# 2026-10-22). Input tokens add a fraction of a cent per call; ignored here.
PRICE_PER_IMAGE = {
	"gemini-3.1-flash-lite-image": {"1K": 0.0336},
	"gemini-nano-banana-2.1": {"1K": 0.0336, "2K": 0.0504, "4K": 0.113},
	"gemini-3.1-flash-image": {"1K": 0.067, "2K": 0.101, "4K": 0.151},
	"gemini-3-pro-image": {"1K": 0.134, "2K": 0.134, "4K": 0.24},
}

# Models that only render at 1K; asking them for 2K/4K is rejected.
ONE_K_ONLY = {"gemini-3.1-flash-lite-image"}


class GeminiError(Exception):
	pass


def resolve_size(model, size):
	return "1K" if model in ONE_K_ONLY else (size or "1K")


def estimate_cost(model, size):
	"""Best-effort USD per call; 0 for models we have no price for."""
	prices = PRICE_PER_IMAGE.get(model) or {}
	return prices.get(resolve_size(model, size)) or prices.get("1K") or 0.0


def edit_image(*, api_key, model, prompt, image_bytes, mime_type="image/jpeg",
		aspect_ratio="3:4", image_size="1K", timeout=110):
	"""Return JPEG bytes for `prompt` applied to `image_bytes`. Raises GeminiError."""
	if not api_key:
		raise GeminiError("No Gemini API key — add one in Akwaaba Studio Settings.")

	body = {
		"model": model,
		"input": [
			{"type": "text", "text": prompt},
			{
				"type": "image",
				"mime_type": mime_type,
				"data": base64.b64encode(image_bytes).decode("ascii"),
			},
		],
		"response_format": {
			"type": "image",
			"mime_type": "image/jpeg",
			"aspect_ratio": aspect_ratio,
			"image_size": resolve_size(model, image_size),
		},
	}

	try:
		resp = requests.post(
			ENDPOINT,
			json=body,
			headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
			timeout=timeout,
		)
	except requests.Timeout:
		raise GeminiError(f"Gemini took longer than {timeout}s — try again.")
	except requests.RequestException as e:
		raise GeminiError(f"Couldn't reach Gemini: {e.__class__.__name__}")

	if resp.status_code != 200:
		raise GeminiError(_http_error(resp))

	try:
		data = resp.json()
	except ValueError:
		raise GeminiError("Gemini returned a non-JSON response.")

	if data.get("status") in ("failed", "cancelled"):
		msgs = "; ".join(e.get("message", "") for e in data.get("errors") or [] if e)
		raise GeminiError(f"Gemini {data['status']}: {msgs or 'no detail'}")

	image_b64, text = _extract(data)
	if not image_b64:
		# Usually a safety refusal or the model answering in words instead.
		raise GeminiError("No image came back" + (f": {text[:300]}" if text else "."))
	return base64.b64decode(image_b64)


def _extract(data):
	"""(last base64 image, joined text) from the final model output.

	Images inside `thought` steps are interim drafts, so only `model_output`
	steps count. Falls back to the SDK-style `output_image` if present.
	"""
	image, texts = None, []
	for step in data.get("steps") or []:
		if step.get("type") != "model_output":
			continue
		for block in step.get("content") or []:
			if block.get("type") == "image" and block.get("data"):
				image = block["data"]
			elif block.get("type") == "text" and block.get("text"):
				texts.append(block["text"])
	if not image:
		image = (data.get("output_image") or {}).get("data")
	return image, " ".join(texts).strip()


def _http_error(resp):
	try:
		err = resp.json().get("error") or {}
		msg = err.get("message") or ""
	except ValueError:
		msg = resp.text[:300]
	hint = {
		400: "request rejected",
		401: "API key not valid",
		403: "API key not allowed (is billing enabled?)",
		404: "model not found",
		429: "rate limit or quota hit — wait a minute",
	}.get(resp.status_code, "error")
	return f"Gemini {resp.status_code} ({hint}): {msg}".strip()
