from datetime import datetime, timedelta
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# Initialize SQLAlchemy
db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    strava_id = db.Column(db.Integer, unique=True, nullable=True)
    access_token = db.Column(db.String(200), nullable=True)
    refresh_token = db.Column(db.String(200), nullable=True)
    token_expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_active = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def update_tokens(self, access_token, refresh_token, expires_in):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
        self.update_activity()
        db.session.commit()
    
    def is_token_expired(self):
        if not self.token_expires_at:
            return True
        return datetime.utcnow() > self.token_expires_at
    
    def update_activity(self):
        """Update the last active timestamp"""
        self.last_active = datetime.utcnow()
        db.session.commit()
    
    def deactivate(self):
        """Deactivate user and clear Strava tokens"""
        self.is_active = False
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None
        db.session.commit()
    
    @classmethod
    def get_oldest_inactive_user(cls, days_inactive=30):
        """Find the oldest inactive user who hasn't been active for X days"""
        cutoff = datetime.utcnow() - timedelta(days=days_inactive)
        return cls.query.filter(
            cls.last_active < cutoff,
            cls.is_active == True
        ).order_by(cls.last_active.asc()).first()
    
    @classmethod
    def get_active_user_count(cls):
        """Get count of active users with valid tokens"""
        return cls.query.filter_by(is_active=True).filter(
            cls.access_token.isnot(None)
        ).count()
