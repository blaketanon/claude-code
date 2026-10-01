import logging

from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker

from .models import Base

log = logging.getLogger(__name__)
Session = scoped_session(sessionmaker(expire_on_commit=False))
engine = None


def make_engine(url):
    kw = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
    eng = create_engine(url, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _rec):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return eng


def init_db(app):
    global engine
    engine = make_engine(app.config["SQLALCHEMY_DATABASE_URI"])
    Session.configure(bind=engine)
    Base.metadata.create_all(engine)

    @app.teardown_appcontext
    def _remove(_exc):
        Session.remove()


def init_standalone(url):
    """For the worker process (no Flask app)."""
    global engine
    engine = make_engine(url)
    Session.configure(bind=engine)
    Base.metadata.create_all(engine)
    return engine
