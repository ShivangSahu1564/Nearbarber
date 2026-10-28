from app.extensions import db


class Service(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("barber_shop.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    duration_minutes = db.Column(db.Integer, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true(), index=True)
    shop = db.relationship("BarberShop", back_populates="services")
    __table_args__ = (db.CheckConstraint("price >= 0", name="ck_service_price"), db.CheckConstraint("duration_minutes > 0", name="ck_service_duration"), db.UniqueConstraint("shop_id", "name", name="uq_service_shop_name"))
