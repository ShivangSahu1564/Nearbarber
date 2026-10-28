from app.extensions import db
from app.models import BarberShop, Service, User


def make_user(app, email, role):
    with app.app_context():
        user = User(name=role.title(), email=email, role=role)
        user.set_password("test-password-123")
        db.session.add(user)
        db.session.commit()
        return user.id


def make_shop(app, owner_id, *, name="Corner Cuts", locality="Green Park", is_open=True):
    with app.app_context():
        shop = BarberShop(owner_id=owner_id, name=name, locality=locality, address="12 Main Road", is_open=is_open)
        db.session.add(shop)
        db.session.commit()
        return shop.id


def sign_in(client, email):
    return client.post("/auth/login", data={"email": email, "password": "test-password-123"})


def test_owner_can_create_shop(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    sign_in(client, "owner@example.com")
    response = client.post("/owner/shops/new", data={
        "name": "Blade & Borough", "locality": "West End", "address": "5 Market Street",
        "contact_phone": "+91 98765 43210", "description": "A neighborhood barber.",
        "opening_time": "09:00", "closing_time": "18:00", "estimated_service_minutes": "35", "is_open": "on",
    })
    assert response.status_code == 302
    with app.app_context():
        shop = db.session.scalar(db.select(BarberShop).where(BarberShop.name == "Blade & Borough"))
        assert shop.owner_id == owner_id
        assert shop.opening_time.strftime("%H:%M") == "09:00"
        assert shop.description == "A neighborhood barber."


def test_customer_cannot_create_shop(client, app):
    make_user(app, "customer@example.com", "customer")
    sign_in(client, "customer@example.com")
    assert client.get("/owner/shops/new").status_code == 403
    assert client.post("/owner/shops/new", data={}).status_code == 403


def test_unauthenticated_user_cannot_create_or_manage_shop(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    shop_id = make_shop(app, owner_id)
    assert client.get("/owner/shops/new").status_code == 302
    assert client.post("/owner/shops/new", data={}).status_code == 302
    assert client.get(f"/owner/shops/{shop_id}/edit").status_code == 302
    assert client.post(f"/owner/shops/{shop_id}/edit", data={}).status_code == 302


def test_owner_can_edit_own_shop(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    shop_id = make_shop(app, owner_id)
    sign_in(client, "owner@example.com")
    response = client.post(f"/owner/shops/{shop_id}/edit", data={
        "name": "Updated Cuts", "locality": "Old Town", "address": "9 New Street",
        "estimated_service_minutes": "40", "is_open": "on",
    })
    assert response.status_code == 302
    with app.app_context():
        shop = db.session.get(BarberShop, shop_id)
        assert shop.name == "Updated Cuts"
        assert shop.locality == "Old Town"


def test_owner_cannot_edit_another_owners_shop(client, app):
    make_user(app, "one@example.com", "owner")
    other_id = make_user(app, "two@example.com", "owner")
    shop_id = make_shop(app, other_id)
    sign_in(client, "one@example.com")
    assert client.get(f"/owner/shops/{shop_id}/edit").status_code == 403
    assert client.post(f"/owner/shops/{shop_id}/edit", data={"name": "Taken", "locality": "A", "address": "B"}).status_code == 403
    with app.app_context():
        assert db.session.get(BarberShop, shop_id).name == "Corner Cuts"


def test_owner_cannot_modify_another_owners_service(client, app):
    make_user(app, "one@example.com", "owner")
    other_id = make_user(app, "two@example.com", "owner")
    shop_id = make_shop(app, other_id)
    with app.app_context():
        service = Service(shop_id=shop_id, name="Fade", price=25, duration_minutes=30)
        db.session.add(service)
        db.session.commit()
        service_id = service.id
    sign_in(client, "one@example.com")
    assert client.get(f"/owner/services/{service_id}/edit").status_code == 403
    assert client.post(f"/owner/services/{service_id}/edit", data={"name": "Hijacked", "price": "1", "duration_minutes": "5"}).status_code == 403
    assert client.post(f"/owner/services/{service_id}/deactivate").status_code == 403
    with app.app_context():
        service = db.session.get(Service, service_id)
        assert service.name == "Fade" and service.is_active


def test_owner_can_manage_services(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    shop_id = make_shop(app, owner_id)
    sign_in(client, "owner@example.com")
    assert client.post(f"/owner/shops/{shop_id}/services/new", data={"name": "Classic Cut", "price": "22.50", "duration_minutes": "30"}).status_code == 302
    with app.app_context():
        service = db.session.scalar(db.select(Service).where(Service.shop_id == shop_id))
        service_id = service.id
    assert client.post(f"/owner/services/{service_id}/edit", data={"name": "Classic Cut Plus", "price": "25", "duration_minutes": "35"}).status_code == 302
    assert client.post(f"/owner/services/{service_id}/deactivate").status_code == 302
    with app.app_context():
        service = db.session.get(Service, service_id)
        assert service.name == "Classic Cut Plus" and service.price == 25 and not service.is_active


def test_public_directory_searches_locality_case_insensitively(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    make_shop(app, owner_id, name="Park Barber", locality="Green Park")
    make_shop(app, owner_id, name="Harbor Barber", locality="Harbor Point")
    response = client.get("/customer/shops?locality=green")
    assert response.status_code == 200
    assert b"Park Barber" in response.data
    assert b"Harbor Barber" not in response.data


def test_inactive_shop_is_hidden_from_public_listing_and_details(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    shop_id = make_shop(app, owner_id, is_open=False)
    assert b"Corner Cuts" not in client.get("/customer/shops").data
    assert client.get(f"/customer/shops/{shop_id}").status_code == 404


def test_public_shop_details_include_only_active_services(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    shop_id = make_shop(app, owner_id)
    with app.app_context():
        db.session.add_all([
            Service(shop_id=shop_id, name="Visible Cut", price=20, duration_minutes=25, is_active=True),
            Service(shop_id=shop_id, name="Private Old Service", price=10, duration_minutes=15, is_active=False),
        ])
        db.session.commit()
    response = client.get(f"/customer/shops/{shop_id}")
    assert response.status_code == 200
    assert b"Visible Cut" in response.data
    assert b"Private Old Service" not in response.data


def test_invalid_shop_data_is_rejected(client, app):
    make_user(app, "owner@example.com", "owner")
    sign_in(client, "owner@example.com")
    response = client.post("/owner/shops/new", data={
        "name": "", "locality": "Area", "address": "Address", "contact_phone": "not a phone",
        "opening_time": "09:00", "closing_time": "", "estimated_service_minutes": "999",
    })
    assert response.status_code == 400
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(BarberShop)) == 0
