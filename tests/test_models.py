import pytest
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models import User, BarberShop, Service, QueueCounter


def test_shop_service_relationships(app):
    with app.app_context():
        user = User(name="Owner", email="owner@example.com", role="owner")
        user.set_password("long-password")
        shop = BarberShop(owner=user, name="North Cuts", locality="North", address="1 Main St")
        service = Service(shop=shop, name="Classic cut", price=20, duration_minutes=30)
        db.session.add_all([user, shop, service])
        db.session.commit()
        assert user.shops[0].name == "North Cuts"
        assert shop.services[0].name == "Classic cut"


def test_duplicate_email_constraint(app):
    with app.app_context():
        db.session.add_all([User(name="One", email="same@example.com", role="customer", password_hash="x"), User(name="Two", email="same@example.com", role="customer", password_hash="x")])
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_shop_requires_existing_owner(app):
    with app.app_context():
        db.session.add(BarberShop(owner_id=9999, name="Orphan", locality="Town", address="Street"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_invalid_role_constraint(app):
    with app.app_context():
        db.session.add(User(name="Bad", email="bad@example.com", role="superuser", password_hash="x"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_shop_queue_counter_relationship(app):
    with app.app_context():
        owner = User(name="Owner", email="owner@example.com", role="owner", password_hash="x")
        shop = BarberShop(owner=owner, name="Shop", locality="Town", address="Street")
        shop.queue_counter = QueueCounter(queue_date=datetime.now(timezone.utc).date(), next_token_number=1)
        db.session.add(shop)
        db.session.commit()
        assert shop.queue_counter.shop_id == shop.id
