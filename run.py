# from flask import render_template
# from datetime import datetime, timedelta

# @app.route("/")
# def index():
#     # Dummy stats + coupons (for UI development only)
#     stats = {
#         "active": 42,
#         "redeemed_today": 8,
#         "expiring_24h": 5
#     }

#     now = datetime.now()
#     coupons = [
#         {"code": "WELCOME10", "description": "10% off first order", "valid_from": "2025-11-30 10:00",
#          "valid_to": "2026-01-01 10:00", "status": "Active"},
#         {"code": "LUNCH50", "description": "₹50 off > ₹300", "valid_from": "2025-12-03 12:00",
#          "valid_to": "2025-12-05 12:00", "status": "Redeemed"},
#         {"code": "FUTURE5", "description": "5% off", "valid_from": "2025-12-10 09:00",
#          "valid_to": "2025-12-20 09:00", "status": "Upcoming"},
#         {"code": "EXPIRED1", "description": "20% off", "valid_from": "2025-10-01 08:00",
#          "valid_to": "2025-10-10 08:00", "status": "Expired"},
#     ]

#     return render_template("dashboard.html",
#                            stats=stats,
#                            coupons=coupons,
#                            generated_at=now.strftime("%Y-%m-%d %H:%M:%S"))

from app import create_app

app = create_app()

if __name__ == "__main__":
    # debug=True only for development
    app.run(host="127.0.0.1", port=5000, debug=True)