from . import db
from datetime import datetime

class Coupon(db.Model):
    __tablename__ = "coupons"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(255))
    issued_at = db.Column(db.DateTime, default=datetime.utcnow)
    valid_from = db.Column(db.DateTime, nullable=True)
    valid_to = db.Column(db.DateTime, nullable=True)
    validity_value = db.Column(db.Integer, nullable=True)   # e.g., 30
    validity_unit = db.Column(db.String(16), default="days") # "days" or "hours"
    issued_to = db.Column(db.String(120), nullable=True)
    tags = db.Column(db.String(255), nullable=True)
    max_redemptions = db.Column(db.Integer, nullable=False, default=1)
    redeemed_count = db.Column(db.Integer, default=0)

    def is_active(self, at=None):
        from datetime import datetime
        at = at or datetime.utcnow()
        if self.valid_from and at < self.valid_from:
            return False
        if self.valid_to and at > self.valid_to:
            return False
        if self.redeemed_count >= (self.max_redemptions or 1):
            return False
        return True

class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    user = db.Column(db.String(80), nullable=True)
    action = db.Column(db.String(80))
    coupon_code = db.Column(db.String(80), nullable=True)
    details = db.Column(db.String(255), nullable=True)
