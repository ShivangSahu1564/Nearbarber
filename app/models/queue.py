from datetime import datetime, timezone
from app.extensions import db


class QueueCounter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("barber_shop.id", ondelete="CASCADE"), nullable=False, unique=True)
    queue_date = db.Column(db.Date, nullable=False)
    next_token_number = db.Column(db.Integer, nullable=False, default=1)
    shop = db.relationship("BarberShop", backref=db.backref("queue_counter", uselist=False))
    __table_args__ = (db.CheckConstraint("next_token_number > 0", name="ck_counter_positive"),)


class QueueToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False, index=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("barber_shop.id", ondelete="RESTRICT"), nullable=False, index=True)
    service_id = db.Column(db.Integer, db.ForeignKey("service.id", ondelete="RESTRICT"), nullable=False)
    token_number = db.Column(db.Integer, nullable=False)
    queue_date = db.Column(db.Date, nullable=False, index=True, default=lambda: datetime.now(timezone.utc).date())
    status = db.Column(db.String(20), nullable=False, default="waiting")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    estimated_wait_minutes = db.Column(db.Integer, nullable=False, default=0)
    customer = db.relationship("User", backref="queue_tokens")
    shop = db.relationship("BarberShop", backref="queue_tokens")
    service = db.relationship("Service", backref="queue_tokens")
    __table_args__ = (
        db.UniqueConstraint("shop_id", "queue_date", "token_number", name="uq_token_shop_day_number"),
        db.Index(
            "uq_queue_active_customer_shop",
            "customer_id", "shop_id", unique=True,
            sqlite_where=db.text("status IN ('waiting', 'in_service')"),
            mssql_where=db.text("status IN ('waiting', 'in_service')"),
        ),
        db.CheckConstraint("status IN ('waiting','in_service','completed','cancelled','skipped')", name="ck_token_status"),
        db.CheckConstraint("token_number > 0 AND estimated_wait_minutes >= 0", name="ck_token_values"),
    )
