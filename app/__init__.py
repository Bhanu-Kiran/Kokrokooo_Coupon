import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

def create_app():
    app = Flask(
        __name__,
        instance_relative_config=True,
        template_folder="templates",
        static_folder="static",
    )

    # ------------------------------------------------------------------
    # Ensure instance folder exists BEFORE constructing database path
    # ------------------------------------------------------------------
    try:
        os.makedirs(app.instance_path, exist_ok=True)
    except Exception:
        pass

    # ------------------------------------------------------------------
    # Build ABSOLUTE path to SQLite DB (important for Windows)
    # ------------------------------------------------------------------
    db_file = os.path.join(app.instance_path, "coupons.db")

    # SQLite URI must use forward slashes and start with sqlite:/// for absolute path
    db_uri = f"sqlite:///{db_file.replace(os.path.sep, '/')}"

    # ------------------------------------------------------------------
    # App configuration
    # ------------------------------------------------------------------
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("COUPONAPP_SECRET", "dev-secret"),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", db_uri),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )

    # ------------------------------------------------------------------
    # Initialize SQLAlchemy AFTER config is applied
    # ------------------------------------------------------------------
    db.init_app(app)

    # ------------------------------------------------------------------
    # Import models & create tables
    # ------------------------------------------------------------------
    with app.app_context():
        from . import models  # ensures models register with SQLAlchemy
        db.create_all()

    # ------------------------------------------------------------------
    # Register routes blueprint
    # ------------------------------------------------------------------
    from .routes import bp
    app.register_blueprint(bp)

    return app