from datetime import datetime, timezone
from app.extensions import db


class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False, index=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("barber_shop.id", ondelete="CASCADE"), nullable=False, index=True)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    customer = db.relationship("User", backref="reviews")
    shop = db.relationship("BarberShop", backref="reviews")
    __table_args__ = (db.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_review_rating"), db.UniqueConstraint("customer_id", "shop_id", name="uq_review_customer_shop"))
