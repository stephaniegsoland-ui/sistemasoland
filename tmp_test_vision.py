import os
import sys
from pathlib import Path
sys.path.insert(0, os.getcwd())
from app.core import vision
from PIL import Image

print('cwd', os.getcwd())
print('torch', __import__('torch').__version__)
print('transformers', __import__('transformers').__version__)
print('cv2', __import__('cv2').__version__)
print('ensure_clip_loaded', vision._ensure_clip_loaded())
print('ensure_image_embedding', vision._ensure_image_embedding_model())
img = Image.new('RGB', (224, 224), color=(128, 128, 128))
emb = vision.get_best_embedding(img)
print('best embedding', type(emb), getattr(emb, 'shape', None))
