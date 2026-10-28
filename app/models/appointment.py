from datetime import datetime, timezone
from app.extensions import db


class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False, index=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("barber_shop.id", ondelete="RESTRICT"), nullable=False, index=True)
    service_id = db.Column(db.Integer, db.ForeignKey("service.id", ondelete="RESTRICT"), nullable=False)
    appointment_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="booked")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    customer = db.relationship("User", backref="appointments")
    shop = db.relationship("BarberShop", backref="appointments")
    service = db.relationship("Service", backref="appointments")
    __table_args__ = (db.CheckConstraint("status IN ('booked','confirmed','completed','cancelled','no_show')", name="ck_appointment_status"),)
