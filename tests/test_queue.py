from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

from app.extensions import db
from app.models import ActivityLog, BarberShop, QueueCounter, QueueToken, Service, User
from app.services import queue as queue_service


def make_user(app, email, role):
    with app.app_context():
        user = User(name=role.title(), email=email, role=role)
        user.set_password("queue-test-password")
        db.session.add(user)
        db.session.commit()
        return user.id


def make_shop(app, owner_id, *, is_open=True):
    with app.app_context():
        shop = BarberShop(owner_id=owner_id, name="Queue Cuts", locality="Central", address="3 Main St", is_open=is_open, estimated_service_minutes=25)
        db.session.add(shop)
        db.session.flush()
        standard = Service(shop_id=shop.id, name="Standard", price=20, duration_minutes=20)
        long = Service(shop_id=shop.id, name="Long Cut", price=30, duration_minutes=30)
        db.session.add_all([standard, long])
        db.session.commit()
        return shop.id, standard.id, long.id


def sign_in(client, email):
    return client.post("/auth/login", data={"email": email, "password": "queue-test-password"})


def token_rows(app):
    with app.app_context():
        return db.session.scalars(db.select(QueueToken).order_by(QueueToken.shop_id, QueueToken.queue_date, QueueToken.token_number)).all()


def test_customer_joins_available_shop_and_gets_daily_token(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    shop_id, service_id, _ = make_shop(app, owner_id)
    customer_id = make_user(app, "customer@example.com", "customer")
    sign_in(client, "customer@example.com")
    response = client.post(f"/customer/shops/{shop_id}/join", data={"service_id": str(service_id)})
    assert response.status_code == 302
    token = token_rows(app)[0]
    assert token.token_number == 1
    assert token.status == "waiting"
    assert token.shop_id == shop_id
    assert token.customer_id == customer_id
    assert token.queue_date == queue_service.utc_business_date()
    assert b"Token #1" in client.get("/customer/tokens").data
    with app.app_context():
        assert db.session.scalar(db.select(ActivityLog).where(ActivityLog.action == "queue_token_created")) is not None


def test_unauthenticated_user_cannot_join(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    shop_id, service_id, _ = make_shop(app, owner_id)
    assert client.post(f"/customer/shops/{shop_id}/join", data={"service_id": service_id}).status_code == 302
    assert token_rows(app) == []


def test_owner_cannot_join_as_customer(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    shop_id, service_id, _ = make_shop(app, owner_id)
    sign_in(client, "owner@example.com")
    assert client.post(f"/customer/shops/{shop_id}/join", data={"service_id": service_id}).status_code == 403
    assert token_rows(app) == []


def test_closed_shop_and_invalid_service_are_rejected(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    closed_shop_id, closed_service_id, _ = make_shop(app, owner_id, is_open=False)
    other_shop_id, _, _ = make_shop(app, owner_id)
    make_user(app, "customer@example.com", "customer")
    sign_in(client, "customer@example.com")
    client.post(f"/customer/shops/{closed_shop_id}/join", data={"service_id": closed_service_id})
    client.post(f"/customer/shops/{other_shop_id}/join", data={"service_id": closed_service_id})
    client.post(f"/customer/shops/{other_shop_id}/join", data={"service_id": "99999"})
    assert token_rows(app) == []


def test_duplicate_active_entry_is_rejected(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    shop_id, service_id, _ = make_shop(app, owner_id)
    make_user(app, "customer@example.com", "customer")
    sign_in(client, "customer@example.com")
    client.post(f"/customer/shops/{shop_id}/join", data={"service_id": service_id})
    response = client.post(f"/customer/shops/{shop_id}/join", data={"service_id": service_id}, follow_redirects=True)
    assert response.status_code == 200
    assert b"already have an active token" in response.data
    assert len(token_rows(app)) == 1


def test_wait_positions_and_estimates_update_as_statuses_change(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    shop_id, standard_id, long_id = make_shop(app, owner_id)
    for number in (1, 2, 3):
        make_user(app, f"person{number}@example.com", "customer")
    for email, service_id in (("person1@example.com", standard_id), ("person2@example.com", long_id), ("person3@example.com", standard_id)):
        sign_in(client, email)
        client.post(f"/customer/shops/{shop_id}/join", data={"service_id": service_id})
        client.post("/auth/logout")
    tokens = token_rows(app)
    assert [token.token_number for token in tokens] == [1, 2, 3]
    with app.app_context():
        token = db.session.get(QueueToken, tokens[2].id)
        assert queue_service.token_position(token) == {"position": 3, "ahead": 2, "wait_minutes": 50}
    sign_in(client, "owner@example.com")
    client.post(f"/owner/queue/{tokens[0].id}/start")
    with app.app_context():
        second = db.session.get(QueueToken, tokens[1].id)
        third = db.session.get(QueueToken, tokens[2].id)
        assert second.estimated_wait_minutes == 20
        assert third.estimated_wait_minutes == 50
    client.post(f"/owner/queue/{tokens[0].id}/complete")
    with app.app_context():
        third = db.session.get(QueueToken, tokens[2].id)
        assert third.estimated_wait_minutes == 30


def test_completed_and_cancelled_tokens_leave_waiting_count(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    shop_id, service_id, _ = make_shop(app, owner_id)
    make_user(app, "one@example.com", "customer")
    make_user(app, "two@example.com", "customer")
    sign_in(client, "one@example.com")
    client.post(f"/customer/shops/{shop_id}/join", data={"service_id": service_id})
    client.post("/auth/logout")
    sign_in(client, "two@example.com")
    client.post(f"/customer/shops/{shop_id}/join", data={"service_id": service_id})
    tokens = token_rows(app)
    client.post(f"/customer/tokens/{tokens[1].id}/cancel")
    client.post("/auth/logout")
    sign_in(client, "owner@example.com")
    client.post(f"/owner/queue/{tokens[0].id}/start")
    client.post(f"/owner/queue/{tokens[0].id}/complete")
    with app.app_context():
        snapshot = queue_service.queue_snapshot(shop_id)
        assert snapshot["waiting_count"] == 0
        assert snapshot["tokens"] == []


def test_owner_can_update_own_queue_and_cannot_update_other_shop(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    other_owner_id = make_user(app, "other@example.com", "owner")
    shop_id, service_id, _ = make_shop(app, owner_id)
    other_shop, other_service, _ = make_shop(app, other_owner_id)
    make_user(app, "customer@example.com", "customer")
    sign_in(client, "customer@example.com")
    client.post(f"/customer/shops/{shop_id}/join", data={"service_id": service_id})
    client.post(f"/customer/shops/{other_shop}/join", data={"service_id": other_service})
    tokens = token_rows(app)
    client.post("/auth/logout")
    sign_in(client, "owner@example.com")
    assert client.get("/owner/queue").status_code == 200
    assert client.post(f"/owner/queue/{tokens[0].id}/start").status_code == 302
    client.post(f"/owner/queue/{tokens[1].id}/start", follow_redirects=True)
    with app.app_context():
        assert db.session.get(QueueToken, tokens[1].id).status == "waiting"


def test_customer_cannot_cancel_another_customers_token(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    shop_id, service_id, _ = make_shop(app, owner_id)
    make_user(app, "one@example.com", "customer")
    make_user(app, "two@example.com", "customer")
    sign_in(client, "one@example.com")
    client.post(f"/customer/shops/{shop_id}/join", data={"service_id": service_id})
    token = token_rows(app)[0]
    client.post("/auth/logout")
    sign_in(client, "two@example.com")
    client.post(f"/customer/tokens/{token.id}/cancel", follow_redirects=True)
    with app.app_context():
        assert db.session.get(QueueToken, token.id).status == "waiting"
        assert b"one@example.com" not in client.get("/customer/tokens").data


def test_invalid_status_transition_is_rejected(client, app):
    owner_id = make_user(app, "owner@example.com", "owner")
    shop_id, service_id, _ = make_shop(app, owner_id)
    make_user(app, "customer@example.com", "customer")
    sign_in(client, "customer@example.com")
    client.post(f"/customer/shops/{shop_id}/join", data={"service_id": service_id})
    token = token_rows(app)[0]
    client.post("/auth/logout")
    sign_in(client, "owner@example.com")
    response = client.post(f"/owner/queue/{token.id}/complete", follow_redirects=True)
    assert b"cannot move from waiting to completed" in response.data
    with app.app_context():
        assert db.session.get(QueueToken, token.id).status == "waiting"


def test_daily_counter_restarts_without_reusing_numbers_within_a_day(app, monkeypatch):
    owner_id = make_user(app, "owner@example.com", "owner")
    shop_id, service_id, _ = make_shop(app, owner_id)
    first_id = make_user(app, "first@example.com", "customer")
    second_id = make_user(app, "second@example.com", "customer")
    third_id = make_user(app, "third@example.com", "customer")
    day_one = datetime(2026, 1, 2, 10, tzinfo=timezone.utc).date()
    day_two = day_one + timedelta(days=1)
    monkeypatch.setattr(queue_service, "utc_business_date", lambda now=None: day_one)
    with app.app_context():
        first = queue_service.join_queue(first_id, shop_id, service_id)
        second = queue_service.join_queue(second_id, shop_id, service_id)
        monkeypatch.setattr(queue_service, "utc_business_date", lambda now=None: day_two)
        third = queue_service.join_queue(third_id, shop_id, service_id)
        assert (first.token_number, second.token_number, third.token_number) == (1, 2, 1)
        assert first.queue_date == second.queue_date == day_one
        assert third.queue_date == day_two
        counter = db.session.scalar(db.select(QueueCounter).where(QueueCounter.shop_id == shop_id))
        assert counter.queue_date == day_two and counter.next_token_number == 2


def test_previous_business_day_active_tokens_expire_on_new_join(app, monkeypatch):
    owner_id = make_user(app, "owner@example.com", "owner")
    shop_id, service_id, _ = make_shop(app, owner_id)
    customer_id = make_user(app, "customer@example.com", "customer")
    day_one = datetime(2026, 2, 3, 10, tzinfo=timezone.utc).date()
    monkeypatch.setattr(queue_service, "utc_business_date", lambda now=None: day_one)
    with app.app_context():
        old_token = queue_service.join_queue(customer_id, shop_id, service_id)
        monkeypatch.setattr(queue_service, "utc_business_date", lambda now=None: day_one + timedelta(days=1))
        new_token = queue_service.join_queue(customer_id, shop_id, service_id)
        assert old_token.status == "cancelled"
        assert new_token.token_number == 1
        logs = db.session.scalars(db.select(ActivityLog).where(ActivityLog.details.like("%business_day_expired%"))).all()
        assert len(logs) == 1


def test_simultaneous_sqlite_joins_allocate_distinct_numbers(app):
    owner_id = make_user(app, "owner@example.com", "owner")
    shop_id, service_id, _ = make_shop(app, owner_id)
    customers = [make_user(app, f"parallel{index}@example.com", "customer") for index in range(2)]
    barrier = Barrier(2)

    def join(customer_id):
        with app.app_context():
            barrier.wait(timeout=5)
            token = queue_service.join_queue(customer_id, shop_id, service_id)
            return token.token_number

    with ThreadPoolExecutor(max_workers=2) as executor:
        numbers = list(executor.map(join, customers))
    assert sorted(numbers) == [1, 2]


def test_csrf_is_enabled_for_queue_join():
    from app import create_app
    class CsrfConfig:
        TESTING = True
        SECRET_KEY = "queue-csrf-test"
        SQLALCHEMY_DATABASE_URI = "sqlite://"
        WTF_CSRF_ENABLED = True
    app = create_app(CsrfConfig)
    response = app.test_client().post("/customer/shops/1/join", data={"service_id": "1"})
    assert response.status_code == 400
