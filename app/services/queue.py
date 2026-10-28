from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import ActivityLog, BarberShop, QueueCounter, QueueToken, Service, User

ACTIVE_STATUSES = ("waiting", "in_service")
ALLOWED_TRANSITIONS = {
    "waiting": {"in_service", "cancelled"},
    "in_service": {"completed"},
    "completed": set(),
    "cancelled": set(),
    "skipped": set(),
}
LOG_ACTIONS = {
    "waiting": "queue_token_created",
    "in_service": "queue_token_started",
    "completed": "queue_token_completed",
    "cancelled": "queue_token_cancelled",
}


class QueueError(Exception):
    """Expected queue operation failure suitable for a user-facing message."""


class DuplicateActiveEntry(QueueError):
    pass


def utc_business_date(now=None):
    """Use UTC calendar dates until shops have an explicit timezone field."""
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()


def _lock_shop_counter_query(shop_id):
    query = db.select(QueueCounter).where(QueueCounter.shop_id == shop_id).with_for_update().execution_options(populate_existing=True)
    # SQL Server ignores FOR UPDATE; these hints serialize existing and absent-key reads.
    return query.with_hint(QueueCounter, "WITH (UPDLOCK, HOLDLOCK, ROWLOCK)", dialect_name="mssql")


def _lock_shop_query(shop_id):
    query = db.select(BarberShop).where(BarberShop.id == shop_id).with_for_update()
    return query.with_hint(BarberShop, "WITH (UPDLOCK, HOLDLOCK, ROWLOCK)", dialect_name="mssql")


def _lock_queue_tokens_query(query):
    return query.with_for_update().with_hint(QueueToken, "WITH (UPDLOCK, ROWLOCK)", dialect_name="mssql").execution_options(populate_existing=True)


def _next_number(shop_id, queue_date):
    counter = db.session.scalar(_lock_shop_counter_query(shop_id))
    max_today = db.session.scalar(
        db.select(func.max(QueueToken.token_number)).where(
            QueueToken.shop_id == shop_id, QueueToken.queue_date == queue_date
        )
    ) or 0
    if counter is None:
        number = max_today + 1
        db.session.add(QueueCounter(shop_id=shop_id, queue_date=queue_date, next_token_number=number + 1))
        return number
    if counter.queue_date != queue_date:
        number = max_today + 1
        counter.queue_date = queue_date
        counter.next_token_number = number + 1
        return number
    number = max(counter.next_token_number, max_today + 1)
    counter.next_token_number = number + 1
    return number


def _active_tokens(shop_id, queue_date):
    return db.session.scalars(
        db.select(QueueToken)
        .options(joinedload(QueueToken.service))
        .where(
            QueueToken.shop_id == shop_id,
            QueueToken.queue_date == queue_date,
            QueueToken.status.in_(ACTIVE_STATUSES),
        )
        .order_by(QueueToken.token_number)
    ).all()


def _refresh_wait_estimates(shop_id, queue_date):
    cumulative_minutes = 0
    active_tokens = _active_tokens(shop_id, queue_date)
    for token in active_tokens:
        token.estimated_wait_minutes = cumulative_minutes if token.status == "waiting" else 0
        # Include the in-service customer at their full duration as a conservative estimate.
        cumulative_minutes += token.service.duration_minutes
    return active_tokens


def queue_snapshot(shop_id, queue_date=None):
    queue_date = queue_date or utc_business_date()
    tokens = _active_tokens(shop_id, queue_date)
    waiting = [token for token in tokens if token.status == "waiting"]
    in_service = next((token for token in tokens if token.status == "in_service"), None)
    return {
        "tokens": tokens,
        "waiting_count": len(waiting),
        "in_service": in_service,
        "next_token_id": waiting[0].id if waiting else None,
        "next_wait_minutes": waiting[0].estimated_wait_minutes if waiting else 0,
        "queue_date": queue_date,
    }


def token_position(token):
    """Return waiting position (1-based), customers ahead, and a duration estimate."""
    if token.status != "waiting" or token.queue_date != utc_business_date():
        return {"position": None, "ahead": 0, "wait_minutes": 0}
    ahead_tokens = db.session.scalars(
        db.select(QueueToken)
        .options(joinedload(QueueToken.service))
        .where(
            QueueToken.shop_id == token.shop_id,
            QueueToken.queue_date == token.queue_date,
            QueueToken.status.in_(ACTIVE_STATUSES),
            QueueToken.token_number < token.token_number,
        )
        .order_by(QueueToken.token_number)
    ).all()
    return {
        "position": len(ahead_tokens) + 1,
        "ahead": len(ahead_tokens),
        "wait_minutes": sum(item.service.duration_minutes for item in ahead_tokens),
    }


def join_queue(customer_id, shop_id, service_id):
    """Allocate a daily shop token and persist token plus activity log atomically."""
    if db.engine.dialect.name == "sqlite":
        # SQLite serializes writes at database scope; BEGIN IMMEDIATE avoids a read-then-write race.
        db.session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    try:
        customer = db.session.get(User, customer_id)
        if customer is None or customer.role != "customer":
            raise QueueError("Only customer accounts can join a queue.")
        shop = db.session.scalar(_lock_shop_query(shop_id))
        if shop is None:
            raise QueueError("That shop could not be found.")
        if not shop.is_open:
            raise QueueError("This shop is not currently accepting queue customers.")
        service = db.session.get(Service, service_id)
        if service is None or service.shop_id != shop.id or not service.is_active:
            raise QueueError("Choose an active service offered by this shop.")
        queue_date = utc_business_date()
        expired = db.session.scalars(
            db.select(QueueToken).where(
                QueueToken.shop_id == shop.id,
                QueueToken.queue_date < queue_date,
                QueueToken.status.in_(ACTIVE_STATUSES),
            )
        ).all()
        for old_token in expired:
            old_token.status = "cancelled"
            db.session.add(ActivityLog(
                user_id=old_token.customer_id,
                action=LOG_ACTIONS["cancelled"],
                details=f"token_id={old_token.id}; shop_id={shop.id}; reason=business_day_expired",
            ))
        existing = db.session.scalar(
            db.select(QueueToken.id).where(
                QueueToken.customer_id == customer_id,
                QueueToken.shop_id == shop_id,
                QueueToken.status.in_(ACTIVE_STATUSES),
            ).limit(1)
        )
        if existing:
            raise DuplicateActiveEntry("You already have an active token for this shop.")

        number = _next_number(shop.id, queue_date)
        token = QueueToken(
            customer_id=customer_id, shop_id=shop.id, service_id=service.id,
            token_number=number, queue_date=queue_date, status="waiting",
            estimated_wait_minutes=0,
        )
        db.session.add(token)
        db.session.flush()
        db.session.add(ActivityLog(user_id=customer_id, action=LOG_ACTIONS["waiting"], details=f"token_id={token.id}; shop_id={shop.id}"))
        _refresh_wait_estimates(shop.id, queue_date)
        db.session.commit()
        return token
    except IntegrityError as error:
        db.session.rollback()
        # Translate the filtered unique-index race into a useful duplicate-entry response.
        duplicate = db.session.scalar(
            db.select(QueueToken.id).where(
                QueueToken.customer_id == customer_id,
                QueueToken.shop_id == shop_id,
                QueueToken.status.in_(ACTIVE_STATUSES),
            ).limit(1)
        )
        if duplicate:
            raise DuplicateActiveEntry("You already have an active token for this shop.") from error
        raise
    except Exception:
        db.session.rollback()
        raise


def transition_token(token_id, actor_id, actor_role, target_status):
    """Apply one allowed status transition, refreshing estimates and logging atomically."""
    if db.engine.dialect.name == "sqlite":
        db.session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    initial = db.session.get(QueueToken, token_id)
    if initial is None:
        raise QueueError("Queue token could not be found.")
    shop = db.session.scalar(_lock_shop_query(initial.shop_id))
    query = db.select(QueueToken).where(QueueToken.id == token_id)
    token = db.session.scalar(_lock_queue_tokens_query(query))
    if token is None:
        raise QueueError("Queue token could not be found.")
    if actor_role == "customer":
        if token.customer_id != actor_id:
            raise QueueError("You cannot manage another customer's token.")
        if target_status != "cancelled":
            raise QueueError("Customers can only cancel a waiting token.")
    elif actor_role == "owner":
        if token.shop.owner_id != actor_id:
            raise QueueError("You cannot manage another shop's queue.")
    else:
        raise QueueError("This account cannot manage queue tokens.")
    if target_status not in ALLOWED_TRANSITIONS.get(token.status, set()):
        raise QueueError(f"A token cannot move from {token.status.replace('_', ' ')} to {target_status.replace('_', ' ')}.")
    if token.queue_date != utc_business_date() and target_status == "in_service":
        raise QueueError("Only today's queue can be started.")
    if target_status == "in_service":
        active = _active_tokens(token.shop_id, token.queue_date)
        if any(item.status == "in_service" for item in active):
            raise QueueError("A customer is already in service.")
        next_waiting = next((item for item in active if item.status == "waiting"), None)
        if next_waiting is None or next_waiting.id != token.id:
            raise QueueError("Call the next waiting token in sequence.")

    token.status = target_status
    db.session.add(ActivityLog(
        user_id=actor_id,
        action=LOG_ACTIONS[target_status],
        details=f"token_id={token.id}; shop_id={token.shop_id}",
    ))
    _refresh_wait_estimates(token.shop_id, token.queue_date)
    db.session.commit()
    return token
