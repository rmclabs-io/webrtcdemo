import os
from pathlib import Path

DB_PATH = "/db/test.db"


class Config(object):
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{str(DB_PATH)}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    AVAILABLE_CAMERAS = ["0"]  # TODO Implement cameras retrieval
    RESOURCES_PATH = (Path(__file__).parent / "static").resolve()
    SAVED_VIDEOS_PATH = Path('/videos').resolve() #FIXME Set definitve video location