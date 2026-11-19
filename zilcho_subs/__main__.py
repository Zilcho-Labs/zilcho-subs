import os
import uvicorn
from .app import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host=os.getenv("ZILCHO_BIND", "127.0.0.1"), port=int(os.getenv("ZILCHO_PORT", "8765")), access_log=False)
