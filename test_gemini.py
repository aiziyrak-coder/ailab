"""ZiyrakAi (Google Generative AI) ulanishini tekshirish — kalitni muhit yoki backend/.env dan oling."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    _here = Path(__file__).resolve().parent
    load_dotenv(_here / "backend" / ".env")
    load_dotenv(_here / ".env")
except ImportError:
    pass

import google.generativeai as genai
from PIL import Image
import numpy as np

key = (os.environ.get("GEMINI_API_KEY") or "").strip()
if not key:
    print("[XATO] GEMINI_API_KEY o'rnatilmagan. .env.example dan nusxa oling.")
    raise SystemExit(1)

genai.configure(api_key=key)
model_id = (os.environ.get("GEMINI_MODEL_ID") or "gemini-2.5-flash").strip()
model = genai.GenerativeModel(model_id)

img = Image.fromarray(np.zeros((200, 200, 3), dtype=np.uint8) + 100)
resp = model.generate_content(["Bu qanday rasm? O'zbek tilida 1 jumlada javob ber.", img])

text = getattr(resp, "text", None) or ""
if not text and resp.candidates:
    parts = resp.candidates[0].content.parts
    text = "".join(getattr(p, "text", "") for p in parts)

print("ZiyrakAi javob:", (text or str(resp))[:200])
print("[OK] ZiyrakAi (API) ishlayapti!")
