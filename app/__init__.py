import os
from flask import Flask, render_template
from config import Config
from app.extensions import db, login_manager, csrf


def create_app(config_object=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object or Config)
    if os.getenv("FLASK_DEBUG", "0").lower() in {"1", "true"}:
        app.config["SESSION_COOKIE_SECURE"] = False
    os.makedirs(app.instance_path, exist_ok=True)
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from app import models  # noqa: F401
    from app.routes.main import main_bp
    from app.routes.auth import auth_bp
    from app.routes.customer import customer_bp
    from app.routes.owner import owner_bp
    from app.routes.admin import admin_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(customer_bp, url_prefix="/customer")
    app.register_blueprint(owner_bp, url_prefix="/owner")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    @app.cli.command("init-db")
    def init_db():
        """Create the development schema; safe to repeat, but not a migration tool."""
        db.create_all()
        print("Database tables created.")

    @app.cli.command("upgrade-shop-schema")
    def upgrade_shop_schema():
        """Safely add shop management fields to an existing database."""
        from app.schema_upgrade import upgrade_shop_management_schema
        upgrade_shop_management_schema()
        print("Shop management schema upgraded; existing data was preserved.")

    @app.cli.command("upgrade-queue-schema")
    def upgrade_queue_schema_command():
        """Safely add daily queue token fields and constraints."""
        from app.schema_upgrade import upgrade_queue_schema
        upgrade_queue_schema()
        print("Queue schema upgraded; existing token records were preserved.")

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(_error):
        db.session.rollback()
        return render_template("errors/500.html"), 500

    return app
