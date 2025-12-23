from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# Initialize SQLAlchemy
db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    email = db.Column(db.String(120), unique=True, nullable=False)
    strava_id = db.Column(db.Integer, unique=True)
    access_token = db.Column(db.String(200))
    refresh_token = db.Column(db.String(200))
    token_expires_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def update_strava_tokens(self, token_response):
        self.access_token = token_response.get('access_token')
        self.refresh_token = token_response.get('refresh_token')
        self.token_expires_at = datetime.fromtimestamp(
            time.time() + token_response.get('expires_in', 21600)
        )
        if 'athlete' in token_response:
            self.strava_id = token_response['athlete'].get('id')
        db.session.commit()

    def is_token_expired(self):
        if not self.token_expires_at:
            return True
        return datetime.utcnow() > self.token_expires_at
