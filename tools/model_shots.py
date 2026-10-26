#!/usr/bin/env python3
"""Turn cloth photos (on a stand, hammock or mannequin) into photos of a person wearing them,
using the Gemini image API ("Nano Banana").

    export GEMINI_API_KEY=...        # from Google AI Studio, never commit it
    python3 -I tools/model_shots.py photos/ out/ [--model MODEL] [--prompt "..."]

Every .jpg/.png in the input folder gets one output image in the output folder.
Existing outputs are skipped, so a rerun only does the missing ones.
"""
import argparse, base64, json, mimetypes, os, sys, urllib.error, urllib.request
from pathlib import Path

# Set to the exact Nano Banana model id shown in AI Studio for your account (override with --model
# or GEMINI_IMAGE_MODEL). The default is the original Nano Banana.
DEFAULT_MODEL = "gemini-2.5-flash-image"
DEFAULT_PROMPT = (
    "This is a photo of a kente cloth hanging on a stand or mannequin. Show a model wearing this "
    "exact cloth as a kente stole/garment, full upper body, natural light, plain studio background, "
    "fashion catalogue style. Keep the cloth's pattern, colours and strip layout exactly as in the "
    "photo; do not invent or change the design."
)


def generate(path, model, prompt, key):
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    body = {"contents": [{"parts": [
        {"text": prompt},
        {"inline_data": {"mime_type": mime, "data": base64.b64encode(path.read_bytes()).decode()}},
    ]}]}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.load(r)
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            img = part.get("inlineData") or part.get("inline_data")
            if img:
                return base64.b64decode(img["data"])
    raise RuntimeError("no image in response: " + json.dumps(data)[:300])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--model", default=os.environ.get("GEMINI_IMAGE_MODEL", DEFAULT_MODEL))
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    a = ap.parse_args()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("Set GEMINI_API_KEY first.")
    a.dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(a.src.iterdir()):
        if p.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        out = a.dst / (p.stem + "-model.png")
        if out.exists():
            continue
        try:
            out.write_bytes(generate(p, a.model, a.prompt, key))
            print("ok  ", p.name)
        except (urllib.error.HTTPError, RuntimeError) as e:
            print("FAIL", p.name, e, file=sys.stderr)


if __name__ == "__main__":
    main()
