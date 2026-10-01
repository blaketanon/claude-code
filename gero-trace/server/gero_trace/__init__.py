"""Gero Trace: lets Salesforce users ask Claude why something happened, across Salesforce,
the FundingMetrics repos and AWS, and turn the answer into an engineering issue that Claude
can fix once approved."""
import logging
import os

from flask import Flask, jsonify

__version__ = "0.1.0"


def create_app(config_overrides=None):
    from . import api, auth, views
    from .config import Config
    from .db import init_db

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)
    init_db(app)
    from .github_client import GitHub
    app.extensions["github"] = GitHub.from_config(app.config)
    auth.init_app(app)
    app.register_blueprint(api.bp)
    app.register_blueprint(views.bp)

    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True, version=__version__)

    return app
