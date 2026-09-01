import sys
import json
from urllib.request import urlopen

from app.core.vision import classify_image_bytes


def download_image_bytes(url: str) -> bytes:
    with urlopen(url, timeout=30) as resp:
        return resp.read()


def main():
    # default sample image (CC0 / Unsplash small car photo)
    sample_url = "https://images.unsplash.com/photo-1549921296-3a2c7a1d15a5?auto=format&fit=crop&w=800&q=80"
    if len(sys.argv) > 1:
        sample_url = sys.argv[1]

    print(f"Downloading sample image: {sample_url}")
    try:
        img_bytes = download_image_bytes(sample_url)
    except Exception as e:
        print("Failed to download image:", e)
        sys.exit(2)

    print("Classifying...")
    res = classify_image_bytes(img_bytes)
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
