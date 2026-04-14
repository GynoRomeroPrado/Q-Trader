import sys
import threading
import time
import logging
import asyncio
import subprocess
import os

import uvicorn

from config.settings import settings
from run_bot import main as bot_main
from services.api_server import app as fastapi_app

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("launcher_gui")

def start_backend():
    """Start the FastAPI backend server on a local thread."""
    logger.info("⚡ Iniciando el Engine HFT Backend...")
    # Run uvicorn programmatically
    config = uvicorn.Config(
        app=fastapi_app,
        host="127.0.0.1",
        port=settings.dashboard.port,
        log_level="error"
    )
    server = uvicorn.Server(config)
    server.run()

if __name__ == "__main__":
    # Start Backend API in background
    backend_thread = threading.Thread(target=start_backend, daemon=True)
    backend_thread.start()

    # Wait 4 seconds for the backend port to bind cleanly to prevent blank screens
    logger.info("Esperando que el backend enlace los puertos correctamente...")
    time.sleep(4)
    
    dashboard_url = f"http://127.0.0.1:{settings.dashboard.port}/"
    
    try:
        import webview
        logger.info("🖥️ Iniciando Ventana Nativa PyWebView...")
        window = webview.create_window(
            'Q-Trader Command Center',
            dashboard_url,
            width=1400,
            height=850,
            min_size=(1024, 768),
            background_color="#0f0f11", 
        )
        webview.start(private_mode=False)
    except ImportError:
        logger.warning("No se encontró pywebview. Haciendo fallback a OS Native App Mode (Edge/Chrome)...")
        if sys.platform == "win32":
            subprocess.Popen(["start", "msedge", f"--app={dashboard_url}"], shell=True)
        else:
            subprocess.Popen(["google-chrome", f"--app={dashboard_url}"])
        
        logger.info("Aplicación iniciada en modo Fallback. Presiona Ctrl+C en esta consola para apagar.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Apagando HFT Bot...")
