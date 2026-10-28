from flask import Blueprint, render_template
from flask_login import login_required
from app.routes import roles_required

admin_bp = Blueprint("admin", __name__)


@admin_bp.get("/dashboard")
@login_required
@roles_required("admin")
def dashboard():
    return render_template("admin/dashboard.html")
