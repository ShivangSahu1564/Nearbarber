from urllib.parse import urljoin, urlsplit
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func
from app.extensions import db
from app.models import User, ActivityLog

auth_bp = Blueprint("auth", __name__)


def safe_next_url(target):
    if not target:
        return None
    ref = urlsplit(request.host_url)
    test = urlsplit(urljoin(request.host_url, target))
    return target if test.scheme in {"http", "https"} and ref.netloc == test.netloc else None


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "customer")
        try:
            email = validate_email(email, check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            flash("Enter a valid email address.", "error")
            return render_template("auth/register.html"), 400
        if not name or len(name) > 120 or len(password) < 8 or role not in {"customer", "owner"}:
            flash("Check the form. Passwords must contain at least 8 characters.", "error")
            return render_template("auth/register.html"), 400
        if db.session.scalar(db.select(User).where(func.lower(User.email) == email)):
            flash("We could not create an account with those details.", "error")
            return render_template("auth/register.html"), 409
        user = User(name=name, email=email, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.add(ActivityLog(user=user, action="account_registered"))
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("We could not create an account with those details.", "error")
            return render_template("auth/register.html"), 409
        login_user(user)
        flash("Your account is ready.", "success")
        return redirect(url_for("customer.dashboard") if role == "customer" else url_for("owner.dashboard"))
    return render_template("auth/register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db.session.scalar(db.select(User).where(func.lower(User.email) == email)) if email else None
        if not user or not user.check_password(password):
            flash("Email or password is incorrect.", "error")
            return render_template("auth/login.html"), 401
        login_user(user)
        destination = safe_next_url(request.args.get("next") or request.form.get("next"))
        if destination:
            return redirect(destination)
        flash("Welcome back.", "success")
        return redirect(url_for("customer.dashboard") if user.role == "customer" else url_for("owner.dashboard") if user.role == "owner" else url_for("admin.dashboard"))
    return render_template("auth/login.html")


@auth_bp.post("/logout")
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("main.index"))
