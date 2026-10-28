import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import BarberShop, Service
from app.routes import roles_required
from app.services.queue import QueueError, queue_snapshot, transition_token

owner_bp = Blueprint("owner", __name__)
PHONE_RE = re.compile(r"^[+0-9().\- ]{7,30}$")


def _shop_for_owner(shop_id):
    shop = db.session.get(BarberShop, shop_id)
    if shop is None:
        abort(404)
    if shop.owner_id != current_user.id:
        abort(403)
    return shop


def _service_for_owner(service_id):
    service = db.session.get(Service, service_id)
    if service is None:
        abort(404)
    _shop_for_owner(service.shop_id)
    return service


def _shop_form_data(shop=None):
    form = {
        "name": request.form.get("name", "").strip(),
        "locality": request.form.get("locality", "").strip(),
        "address": request.form.get("address", "").strip(),
        "contact_phone": request.form.get("contact_phone", "").strip(),
        "description": request.form.get("description", "").strip(),
        "opening_time": request.form.get("opening_time", "").strip(),
        "closing_time": request.form.get("closing_time", "").strip(),
        "estimated_service_minutes": request.form.get("estimated_service_minutes", "30").strip(),
        "is_open": request.form.get("is_open") == "on",
    }
    errors = []
    for key, label, limit in (("name", "Shop name", 160), ("locality", "Locality", 120), ("address", "Address", 300)):
        if not form[key]:
            errors.append(f"{label} is required.")
        elif len(form[key]) > limit:
            errors.append(f"{label} must be {limit} characters or fewer.")
    phone_digits = sum(character.isdigit() for character in form["contact_phone"])
    if form["contact_phone"] and (not PHONE_RE.fullmatch(form["contact_phone"]) or not 7 <= phone_digits <= 15):
        errors.append("Enter a valid contact number (7–30 digits or common phone punctuation).")
    if len(form["description"]) > 2000:
        errors.append("Description must be 2,000 characters or fewer.")
    try:
        minutes = int(form["estimated_service_minutes"])
        if not 1 <= minutes <= 480:
            raise ValueError
    except ValueError:
        minutes = 30
        errors.append("Default service duration must be between 1 and 480 minutes.")
    parsed_times = []
    for value in (form["opening_time"], form["closing_time"]):
        try:
            parsed_times.append(datetime.strptime(value, "%H:%M").time() if value else None)
        except ValueError:
            parsed_times.append(None)
            errors.append("Enter opening and closing times in a valid 24-hour format.")
    if bool(form["opening_time"]) != bool(form["closing_time"]):
        errors.append("Provide both opening and closing times, or leave both blank.")
    return form, errors, minutes, parsed_times


@owner_bp.get("/dashboard")
@login_required
@roles_required("owner")
def dashboard():
    shops = db.session.scalars(db.select(BarberShop).where(BarberShop.owner_id == current_user.id).order_by(BarberShop.name)).all()
    return render_template("owner/dashboard.html", shops=shops)


@owner_bp.route("/shops/new", methods=["GET", "POST"])
@login_required
@roles_required("owner")
def create_shop():
    if request.method == "POST":
        form, errors, minutes, times = _shop_form_data()
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("owner/shop_form.html", shop=None, form=form), 400
        shop = BarberShop(
            owner_id=current_user.id,
            name=form["name"], locality=form["locality"], address=form["address"],
            contact_phone=form["contact_phone"] or None, description=form["description"] or None,
            opening_time=times[0], closing_time=times[1], estimated_service_minutes=minutes,
            is_open=form["is_open"],
        )
        db.session.add(shop)
        db.session.commit()
        flash("Your shop has been created.", "success")
        return redirect(url_for("owner.shop_detail", shop_id=shop.id))
    return render_template("owner/shop_form.html", shop=None, form=None)


@owner_bp.get("/shops/<int:shop_id>")
@login_required
@roles_required("owner")
def shop_detail(shop_id):
    shop = _shop_for_owner(shop_id)
    return render_template("owner/shop_detail.html", shop=shop)


@owner_bp.route("/shops/<int:shop_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("owner")
def edit_shop(shop_id):
    shop = _shop_for_owner(shop_id)
    if request.method == "POST":
        form, errors, minutes, times = _shop_form_data()
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("owner/shop_form.html", shop=shop, form=form), 400
        shop.name, shop.locality, shop.address = form["name"], form["locality"], form["address"]
        shop.contact_phone = form["contact_phone"] or None
        shop.description = form["description"] or None
        shop.opening_time, shop.closing_time = times
        shop.estimated_service_minutes, shop.is_open = minutes, form["is_open"]
        db.session.commit()
        flash("Shop details updated.", "success")
        return redirect(url_for("owner.shop_detail", shop_id=shop.id))
    return render_template("owner/shop_form.html", shop=shop, form=None)


@owner_bp.route("/shops/<int:shop_id>/services/new", methods=["GET", "POST"])
@login_required
@roles_required("owner")
def create_service(shop_id):
    shop = _shop_for_owner(shop_id)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        try:
            price = Decimal(request.form.get("price", ""))
            duration = int(request.form.get("duration_minutes", ""))
            if not price.is_finite() or price < 0 or price > Decimal("99999999.99"):
                raise ValueError
            if not 1 <= duration <= 480:
                raise ValueError
        except (InvalidOperation, ValueError):
            flash("Enter a valid non-negative price and a duration from 1 to 480 minutes.", "error")
            return render_template("owner/service_form.html", shop=shop, service=None), 400
        if not name or len(name) > 120:
            flash("Service name is required and must be 120 characters or fewer.", "error")
            return render_template("owner/service_form.html", shop=shop, service=None), 400
        service = Service(shop=shop, name=name, price=price, duration_minutes=duration)
        db.session.add(service)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("A service with that name already exists for this shop.", "error")
            return render_template("owner/service_form.html", shop=shop, service=None), 409
        flash("Service added.", "success")
        return redirect(url_for("owner.shop_detail", shop_id=shop.id))
    return render_template("owner/service_form.html", shop=shop, service=None)


@owner_bp.route("/services/<int:service_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("owner")
def edit_service(service_id):
    service = _service_for_owner(service_id)
    shop = service.shop
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        try:
            price = Decimal(request.form.get("price", ""))
            duration = int(request.form.get("duration_minutes", ""))
            if not price.is_finite() or price < 0 or price > Decimal("99999999.99") or not 1 <= duration <= 480:
                raise ValueError
        except (InvalidOperation, ValueError):
            flash("Enter a valid non-negative price and a duration from 1 to 480 minutes.", "error")
            return render_template("owner/service_form.html", shop=shop, service=service), 400
        if not name or len(name) > 120:
            flash("Service name is required and must be 120 characters or fewer.", "error")
            return render_template("owner/service_form.html", shop=shop, service=service), 400
        service.name, service.price, service.duration_minutes = name, price, duration
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("A service with that name already exists for this shop.", "error")
            return render_template("owner/service_form.html", shop=shop, service=service), 409
        flash("Service updated.", "success")
        return redirect(url_for("owner.shop_detail", shop_id=shop.id))
    return render_template("owner/service_form.html", shop=shop, service=service)


@owner_bp.post("/services/<int:service_id>/deactivate")
@login_required
@roles_required("owner")
def deactivate_service(service_id):
    service = _service_for_owner(service_id)
    service.is_active = False
    db.session.commit()
    flash("Service deactivated. It is hidden from the public directory.", "success")
    return redirect(url_for("owner.shop_detail", shop_id=service.shop_id))


@owner_bp.get("/queue")
@login_required
@roles_required("owner")
def queue():
    shops = db.session.scalars(
        db.select(BarberShop).where(BarberShop.owner_id == current_user.id).order_by(BarberShop.name)
    ).all()
    queues = [{"shop": shop, **queue_snapshot(shop.id)} for shop in shops]
    return render_template("owner/queue.html", queues=queues)


@owner_bp.post("/queue/<int:token_id>/<action>")
@login_required
@roles_required("owner")
def update_queue_token(token_id, action):
    targets = {"start": "in_service", "complete": "completed", "cancel": "cancelled"}
    target = targets.get(action)
    if target is None:
        abort(404)
    try:
        transition_token(token_id, current_user.id, "owner", target)
    except QueueError as error:
        db.session.rollback()
        flash(str(error), "error")
        return redirect(url_for("owner.queue"))
    flash({"in_service": "Token called and marked in service.", "completed": "Visit marked completed.", "cancelled": "Token cancelled."}[target], "success")
    return redirect(url_for("owner.queue"))
