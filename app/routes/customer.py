from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from app.extensions import db
from app.models import BarberShop, QueueToken
from app.routes import roles_required
from app.services.queue import DuplicateActiveEntry, QueueError, join_queue, token_position, transition_token, utc_business_date

customer_bp = Blueprint("customer", __name__)


@customer_bp.get("/dashboard")
@login_required
@roles_required("customer")
def dashboard():
    return render_template("customer/dashboard.html")


@customer_bp.get("/shops")
def shops():
    query = db.select(BarberShop).where(BarberShop.is_open.is_(True))
    locality = request.args.get("locality", "").strip()[:120]
    if locality:
        query = query.where(BarberShop.locality.ilike(f"%{locality}%"))
    shops = db.session.scalars(query.order_by(BarberShop.name).limit(100)).all()
    return render_template("customer/shops.html", shops=shops, locality=locality)


@customer_bp.get("/shops/<int:shop_id>")
def shop_detail(shop_id):
    shop = db.session.get(BarberShop, shop_id)
    if shop is None or not shop.is_open:
        abort(404)
    return render_template("customer/shop_detail.html", shop=shop)


@customer_bp.post("/shops/<int:shop_id>/join")
@login_required
@roles_required("customer")
def join_shop_queue(shop_id):
    service_value = request.form.get("service_id", "")
    try:
        service_id = int(service_value)
        token = join_queue(current_user.id, shop_id, service_id)
        position = token_position(token)
        flash(
            f"You're in! Your token is #{token.token_number}. "
            f"There are {position['ahead']} customers ahead; estimated wait: {position['wait_minutes']} minutes."
            , "success",
        )
        return redirect(url_for("customer.my_tokens"))
    except (ValueError, QueueError) as error:
        db.session.rollback()
        message = str(error) if isinstance(error, QueueError) else "Choose a valid service."
        flash(message, "error")
        return redirect(url_for("customer.shop_detail", shop_id=shop_id))


@customer_bp.post("/tokens/<int:token_id>/cancel")
@login_required
@roles_required("customer")
def cancel_my_token(token_id):
    try:
        transition_token(token_id, current_user.id, "customer", "cancelled")
    except QueueError as error:
        db.session.rollback()
        flash(str(error), "error")
        return redirect(url_for("customer.my_tokens"))
    flash("Your queue token has been cancelled.", "success")
    return redirect(url_for("customer.my_tokens"))


@customer_bp.get("/tokens")
@login_required
@roles_required("customer")
def my_tokens():
    tokens = db.session.scalars(
        db.select(QueueToken).where(QueueToken.customer_id == current_user.id).order_by(QueueToken.created_at.desc())
    ).all()
    positions = {token.id: token_position(token) for token in tokens if token.queue_date == utc_business_date()}
    return render_template("customer/my_tokens.html", tokens=tokens, positions=positions)
