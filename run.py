"""Entry point for AutoApply BW.

Development:   python run.py            (Flask dev server, auto-reload)
Production:    python run.py --serve    (Waitress WSGI server)
"""
import os
import sys
from app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    if "--serve" in sys.argv:
        from waitress import serve
        print(f" * AutoApply BW running on http://{host}:{port} (waitress)")
        serve(app, host=host, port=port)
    else:
        app.run(host=host, port=port, debug=True)
