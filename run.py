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
        # Requests spend most of their time waiting on I/O (AI calls, job
        # sources, SMTP), so a generous thread count keeps the site responsive
        # while slow requests are in flight. Override with WAITRESS_THREADS.
        threads = int(os.getenv("WAITRESS_THREADS", "16"))
        print(f" * AutoApply BW running on http://{host}:{port} (waitress, {threads} threads)")
        serve(app, host=host, port=port, threads=threads)
    else:
        app.run(host=host, port=port, debug=True, threaded=True)
