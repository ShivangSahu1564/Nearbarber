from datetime import datetime, timezone
from app.extensions import db


class BarberShop(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False, index=True)
    locality = db.Column(db.String(120), nullable=False, index=True)
    address = db.Column(db.String(300), nullable=False)
    contact_phone = db.Column(db.String(30))
    description = db.Column(db.Text)
    opening_time = db.Column(db.Time)
    closing_time = db.Column(db.Time)
    is_open = db.Column(db.Boolean, nullable=False, default=True)
    estimated_service_minutes = db.Column(db.Integer, nullable=False, default=30)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    owner = db.relationship("User", backref=db.backref("shops", lazy=True))
    services = db.relationship("Service", back_populates="shop", cascade="all, delete-orphan")
    __table_args__ = (
        db.CheckConstraint("estimated_service_minutes > 0", name="ck_shop_service_minutes"),
        db.CheckConstraint("(opening_time IS NULL AND closing_time IS NULL) OR (opening_time IS NOT NULL AND closing_time IS NOT NULL)", name="ck_shop_hours_pair"),
    )
