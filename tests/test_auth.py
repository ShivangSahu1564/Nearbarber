from app.extensions import db
from app.models import User


def test_registration_page_renders(client):
    response = client.get("/auth/register")
    assert response.status_code == 200
    assert b"Create your account" in response.data


def test_valid_registration_hashes_password(client, app):
    response = client.post("/auth/register", data={"name": "Asha", "email": "asha@example.com", "password": "long-password", "role": "customer"}, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "asha@example.com"))
        assert user is not None
        assert user.password_hash != "long-password"
        assert user.check_password("long-password")


def test_duplicate_email_rejected_generically(client):
    client.post("/auth/register", data={"name": "Asha", "email": "asha@example.com", "password": "long-password", "role": "customer"})
    client.post("/auth/logout")
    response = client.post("/auth/register", data={"name": "Other", "email": "ASHA@example.com", "password": "long-password", "role": "customer"})
    assert response.status_code == 409
    assert b"could not create an account" in response.data


def test_valid_login(client):
    client.post("/auth/register", data={"name": "Asha", "email": "asha@example.com", "password": "long-password", "role": "customer"})
    client.post("/auth/logout")
    response = client.post("/auth/login", data={"email": "asha@example.com", "password": "long-password"}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Welcome, Asha" in response.data


def test_invalid_login_is_generic(client):
    response = client.post("/auth/login", data={"email": "nobody@example.com", "password": "bad"})
    assert response.status_code == 401
    assert b"Email or password is incorrect" in response.data


def test_external_next_url_is_ignored(client):
    client.post("/auth/register", data={"name": "Asha", "email": "asha@example.com", "password": "long-password", "role": "customer"})
    client.post("/auth/logout")
    response = client.post("/auth/login?next=https://evil.example", data={"email": "asha@example.com", "password": "long-password"})
    assert response.status_code == 302
    assert "evil.example" not in response.headers["Location"]


def test_csrf_enabled_by_default():
    from app import create_app
    app = create_app()
    assert app.config["WTF_CSRF_ENABLED"] is True


def test_csrf_rejects_post_without_token():
    from app import create_app
    class CsrfConfig:
        TESTING = True
        SECRET_KEY = "csrf-test-secret"
        SQLALCHEMY_DATABASE_URI = "sqlite://"
        WTF_CSRF_ENABLED = True
    app = create_app(CsrfConfig)
    response = app.test_client().post("/auth/login", data={"email": "person@example.com", "password": "irrelevant"})
    assert response.status_code == 400
