"""Elastic Beanstalk / gunicorn entry point for the web process (see Procfile).
The investigation worker is a separate process: `python -m gero_trace.worker`."""
from gero_trace import create_app

application = create_app()

if __name__ == "__main__":
    application.run(host="127.0.0.1", port=int(__import__("os").environ.get("PORT", 5056)), debug=True)
