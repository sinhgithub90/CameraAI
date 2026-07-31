"""Upload an image to the demo API.

Usage:
    python examples/demo_client.py path/to/frame.jpg [camera_id]
"""
from __future__ import annotations

import sys

import requests

API_URL = "http://127.0.0.1:8000/analyze/image"


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    image_path = sys.argv[1]
    camera_id = sys.argv[2] if len(sys.argv) > 2 else "cam_demo"

    with open(image_path, "rb") as fh:
        files = {"file": (image_path, fh, "application/octet-stream")}
        data = {"camera_id": camera_id}
        resp = requests.post(API_URL, files=files, data=data, timeout=180)

    resp.raise_for_status()
    print(resp.json())


if __name__ == "__main__":
    main()
