import pytest
from app import create_app
from app.extensions import db


@pytest.fixture
def app(tmp_path):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "test-secret-key"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'test.db'}"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        WTF_CSRF_ENABLED = False  # Isolated tests focus on routes; CSRF stays enabled in app defaults.
        SESSION_COOKIE_SECURE = False
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()
