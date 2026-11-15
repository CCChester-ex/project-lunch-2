"""
Render / Railway / 雲端部署相容入口點
支援：
  - uvicorn main:app --host 0.0.0.0 --port $PORT
  - uvicorn backend.main:app --host 0.0.0.0 --port $PORT
  - gunicorn -k uvicorn.workers.UvicornWorker main:app
"""
import os
from backend.main import app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port)
