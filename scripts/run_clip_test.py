from app.core.vision import classify_image_bytes, _ensure_clip_loaded

if __name__ == '__main__':
    path = 'app/static/inspections/47dc7ef4564741bd8f054fc86067cc6e_before.jpg'
    print('Using sample:', path)
    try:
        with open(path, 'rb') as f:
            b = f.read()
    except Exception as e:
        print('Failed to open sample image:', e)
        raise

    # try ensuring clip is loaded
    loaded = _ensure_clip_loaded()
    print('CLIP loaded:', loaded)
    res = classify_image_bytes(b)
    print('Classification result:', res)
