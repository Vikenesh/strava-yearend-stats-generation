import os
import requests
import json
import logging
from flask import Flask, request, session, redirect, url_for, jsonify, flash, render_template_string, current_app
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import requests
import os
import logging
import calendar
import time
from datetime import datetime, timedelta, timezone
import json
import csv
import io
import base64
import openai
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///strava_users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Import models after db initialization to avoid circular imports
from models import User

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Strava API configuration
STRAVA_CLIENT_ID = os.getenv('STRAVA_CLIENT_ID')
STRAVA_CLIENT_SECRET = os.getenv('STRAVA_CLIENT_SECRET')
STRAVA_AUTHORIZE_URL = 'https://www.strava.com/oauth/authorize'
STRAVA_TOKEN_URL = 'https://www.strava.com/oauth/token'
STRAVA_API_BASE_URL = 'https://www.strava.com/api/v3'
STRAVA_SCOPE = 'activity:read_all,profile:read_all'

# App configuration
APP_URL = os.getenv('APP_URL', 'http://localhost:5000')
CALLBACK_URL = f"{APP_URL}/callback"

# Other configuration
MAX_ACTIVE_USERS = 10  # Maximum number of active users
INACTIVITY_DAYS = 30   # Days of inactivity before considering a user inactive

# Define the STATS_TEMPLATE at the module level
STATS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Your Running Stats</title>
    <style>
        /* Add your CSS styles here */
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; }
        .stats-container { display: flex; justify-content: space-around; margin: 20px 0; }
        .stat-box { text-align: center; padding: 20px; background: #f8f9fa; border-radius: 10px; }
        .stat-value { font-size: 2rem; font-weight: bold; color: #4a5568; }
        .stat-label { color: #718096; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }
        th { background-color: #f8fafc; }
    </style>
</head>
<body>
    <h1>Welcome, {{ athlete_name }}!</h1>
    <div class="stats-container">
        <div class="stat-box">
            <div class="stat-value">{{ total_runs }}</div>
            <div class="stat-label">Total Runs</div>
        </div>
        <div class="stat-box">
            <div class="stat-value">{{ "%.1f"|format(total_distance) }} km</div>
            <div class="stat-label">Total Distance</div>
        </div>
        <div class="stat-box">
            <div class="stat-value">{{ "%.1f"|format(total_time) }} hours</div>
            <div class="stat-label">Total Time</div>
        </div>
    </div>
    <h2>Your Runs in 2025</h2>
    <table>
        <thead>
            <tr>
                <th>Date</th>
                <th>Name</th>
                <th>Distance (km)</th>
                <th>Time</th>
                <th>Pace (min/km)</th>
            </tr>
        </thead>
        <tbody>
            {{ table_rows|safe }}
        </tbody>
    </table>
    <p><small>Generated on {{ generated_at }}</small></p>
</body>
</html>
"""

def revoke_strava_access(user):
    """Revoke Strava access for a user"""
    try:
        # Revoke the access token with Strava
        revoke_url = 'https://www.strava.com/oauth/deauthorize'
        response = requests.post(
            revoke_url,
            data={'access_token': user.access_token}
        )
        
        if response.status_code == 200:
            logger.info(f"Successfully revoked Strava access for user {user.id}")
        else:
            logger.warning(f"Failed to revoke Strava access for user {user.id}: {response.status_code} - {response.text}")
        
    except Exception as e:
        logger.error(f"Error revoking Strava access for user {user.id}: {str(e)}")
    finally:
        # Clear the tokens in the database
        user.access_token = None
        user.refresh_token = None
        user.token_expires_at = None
        db.session.commit()

def utc_to_ist(utc_datetime_str):
    """Convert UTC datetime string to IST timezone"""
    utc_dt = datetime.fromisoformat(utc_datetime_str.replace('Z', '+00:00'))
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    return utc_dt.astimezone(ist_tz)

def analyze_wrapped_stats(activities):
    """Analyze activities for wrapped-style visualization"""
    if not activities:
        return {}
    
    # Initialize stats dictionary
    stats = {
        'total_activities': 0,
        'total_distance_km': 0,
        'total_moving_time_seconds': 0,
        'activities_by_type': {},
        'activities_by_month': {month: 0 for month in range(1, 13)},
        'activities_by_weekday': {day: 0 for day in range(7)},
        'activities_by_hour': {hour: 0 for hour in range(24)},
        'longest_run': None,
        'fastest_run': None,
        'monthly_stats': {month: {'distance': 0, 'count': 0} for month in range(1, 13)},
        'weekly_stats': {day: {'distance': 0, 'count': 0} for day in range(7)},
        'hourly_stats': {hour: {'distance': 0, 'count': 0} for hour in range(24)}
    }
    
    for activity in activities:
        try:
            # Skip if not a run
            if activity.get('type', '').lower() != 'run':
                continue
                
            # Basic activity stats
            stats['total_activities'] += 1
            
            # Distance in km
            distance_km = activity.get('distance', 0) / 1000
            stats['total_distance_km'] += distance_km
            
            # Moving time in seconds
            moving_time = activity.get('moving_time', 0)
            stats['total_moving_time_seconds'] += moving_time
            
            # Activities by type
            activity_type = activity.get('type', 'unknown').lower()
            stats['activities_by_type'][activity_type] = stats['activities_by_type'].get(activity_type, 0) + 1
            
            # Parse start date
            start_date = datetime.fromisoformat(activity.get('start_date', '').replace('Z', '+00:00'))
            
            # Activities by month (1-12)
            month = start_date.month
            stats['activities_by_month'][month] += 1
            stats['monthly_stats'][month]['distance'] += distance_km
            stats['monthly_stats'][month]['count'] += 1
            
            # Activities by weekday (0=Monday, 6=Sunday)
            weekday = start_date.weekday()
            stats['activities_by_weekday'][weekday] += 1
            stats['weekly_stats'][weekday]['distance'] += distance_km
            stats['weekly_stats'][weekday]['count'] += 1
            
            # Activities by hour of day
            hour = start_date.hour
            stats['activities_by_hour'][hour] += 1
            stats['hourly_stats'][hour]['distance'] += distance_km
            stats['hourly_stats'][hour]['count'] += 1
            
            # Track longest run
            if stats['longest_run'] is None or distance_km > stats['longest_run'].get('distance_km', 0):
                stats['longest_run'] = {
                    'name': activity.get('name', 'Unnamed Run'),
                    'distance_km': distance_km,
                    'date': start_date.strftime('%Y-%m-%d'),
                    'id': activity.get('id')
                }
            
            # Track fastest run (by average speed)
            avg_speed = activity.get('average_speed', 0)  # meters per second
            if avg_speed > 0:  # Only consider runs with valid speed
                pace_seconds_per_km = 1000 / avg_speed  # seconds per kilometer
                pace_min_per_km = pace_seconds_per_km / 60  # minutes per kilometer
                
                if stats['fastest_run'] is None or pace_min_per_km < stats['fastest_run'].get('pace_min_per_km', float('inf')):
                    stats['fastest_run'] = {
                        'name': activity.get('name', 'Unnamed Run'),
                        'pace_min_per_km': pace_min_per_km,
                        'pace_formatted': f"{int(pace_min_per_km)}:{int((pace_min_per_km % 1) * 60):02d} min/km",
                        'distance_km': distance_km,
                        'date': start_date.strftime('%Y-%m-%d'),
                        'id': activity.get('id')
                    }
                    
        except Exception as e:
            logger.error(f"Error processing activity {activity.get('id')}: {str(e)}")
    
    # Calculate additional stats
    stats['total_moving_time_hours'] = stats['total_moving_time_seconds'] / 3600
    stats['avg_distance_per_run_km'] = stats['total_distance_km'] / stats['total_activities'] if stats['total_activities'] > 0 else 0
    stats['avg_pace_min_per_km'] = (stats['total_moving_time_seconds'] / 60) / stats['total_distance_km'] if stats['total_distance_km'] > 0 else 0
    
    # Format pace as MM:SS
    if stats['avg_pace_min_per_km'] > 0:
        minutes = int(stats['avg_pace_min_per_km'])
        seconds = int((stats['avg_pace_min_per_km'] % 1) * 60)
        stats['avg_pace_formatted'] = f"{minutes}:{seconds:02d} min/km"
    else:
        stats['avg_pace_formatted'] = "N/A"
    
    return stats

def refresh_access_token():
    """Refresh the access token using the refresh token"""
    if 'refresh_token' not in session:
        logger.error("No refresh token in session")
        return None
        
    try:
        response = requests.post(
            STRAVA_TOKEN_URL,
            data={
                'client_id': STRAVA_CLIENT_ID,
                'client_secret': STRAVA_CLIENT_SECRET,
                'grant_type': 'refresh_token',
                'refresh_token': session['refresh_token']
            }
        )
        
        if response.status_code == 200:
            token_data = response.json()
            
            # Update session with new tokens
            session['access_token'] = token_data['access_token']
            session['refresh_token'] = token_data.get('refresh_token', session['refresh_token'])
            session['token_expires_at'] = time.time() + token_data['expires_in']
            
            logger.info("Successfully refreshed access token")
            return token_data['access_token']
        else:
            logger.error(f"Failed to refresh token: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Error refreshing token: {str(e)}")
        return None

def get_valid_access_token():
    """Get a valid access token, refreshing if necessary"""
    if 'access_token' not in session:
        return None
        
    # Check if token is expired or about to expire (within 5 minutes)
    if time.time() >= session.get('token_expires_at', 0) - 300:
        logger.info("Access token expired or about to expire, refreshing...")
        return refresh_access_token()
    
    return session['access_token']

def get_all_activities():
    """Fetch all activities from Strava API with pagination"""
    access_token = get_valid_access_token()
    if not access_token:
        logger.error("No valid access token available")
        return None
    
    all_activities = []
    page = 1
    per_page = 200  # Maximum allowed by Strava API
    
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    
    while True:
        try:
            # Get activities with pagination
            response = requests.get(
                f'{STRAVA_API_BASE_URL}/athlete/activities',
                headers=headers,
                params={
                    'page': page,
                    'per_page': per_page
                }
            )
            
            if response.status_code == 200:
                activities = response.json()
                if not activities:  # No more activities
                    break
                    
                all_activities.extend(activities)
                logger.info(f"Fetched page {page} with {len(activities)} activities")
                
                # If we got fewer activities than requested, we've reached the end
                if len(activities) < per_page:
                    break
                    
                page += 1
                
            elif response.status_code == 401:  # Unauthorized, try to refresh token
                logger.warning("Token expired, attempting to refresh...")
                new_token = refresh_access_token()
                if new_token:
                    headers['Authorization'] = f'Bearer {new_token}'
                    continue  # Retry the same page with new token
                else:
                    logger.error("Failed to refresh token")
                    return None
                    
            else:
                logger.error(f"Error fetching activities: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Exception while fetching activities: {str(e)}")
            return None
    
    logger.info(f"Total activities fetched: {len(all_activities)}")
    return all_activities

def analyze_with_chatgpt(activities, athlete_name):
    """Analyze activities using ChatGPT API"""
    if not activities:
        return "No activities to analyze."
    
    try:
        # Prepare the prompt for ChatGPT
        prompt = f"""Analyze the following running activities for {athlete_name} and provide insights:
        
        Total Activities: {len(activities)}
        
        Activities (Date, Name, Distance, Moving Time, Average Speed, Max Speed):
        """
        
        # Add activity details to the prompt
        for i, activity in enumerate(activities[:50]):  # Limit to first 50 activities to avoid token limits
            try:
                date = activity.get('start_date', 'N/A')
                name = activity.get('name', 'Unnamed Activity')
                distance = activity.get('distance', 0) / 1000  # Convert to km
                moving_time = activity.get('moving_time', 0)
                avg_speed = activity.get('average_speed', 0) * 3.6  # Convert to km/h
                max_speed = activity.get('max_speed', 0) * 3.6  # Convert to km/h
                
                prompt += f"\n- {date}: {name} | {distance:.1f} km | {moving_time//3600}h {(moving_time%3600)//60}m | {avg_speed:.1f} km/h (avg) | {max_speed:.1f} km/h (max)"
            except Exception as e:
                logger.error(f"Error formatting activity {i}: {str(e)}")
                continue
        
        # Add analysis instructions
        prompt += """
        
        Please provide insights on:
        1. Training volume and consistency
        2. Performance trends over time
        3. Notable achievements or milestones
        4. Any patterns in training (time of day, day of week, etc.)
        5. Suggestions for improvement
        
        Format the response in a clear, structured way with sections and bullet points.
        """
        
        # Call ChatGPT API
        openai.api_key = os.getenv('OPENAI_API_KEY')
        
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful running coach analyzing training data."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,
            temperature=0.7
        )
        
        # Extract and return the analysis
        analysis = response.choices[0].message.content.strip()
        return analysis
        
    except Exception as e:
        logger.error(f"Error in ChatGPT analysis: {str(e)}")
        return f"An error occurred while analyzing your activities: {str(e)}"

# User Authentication Routes

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Check if username already exists
        if User.query.filter_by(username=username).first():
            flash('Username already exists. Please choose a different one.', 'error')
            return redirect(url_for('register'))
        
        # Create new user
        hashed_password = generate_password_hash(password, method='sha256')
        new_user = User(
            username=username,
            password=hashed_password,
            is_active=True,
            last_active=datetime.utcnow(),
            created_at=datetime.utcnow()
        )
        
        # Make space for new user if needed
        if User.query.filter_by(is_active=True).count() >= MAX_ACTIVE_USERS:
            make_space_for_new_user()
        
        db.session.add(new_user)
        db.session.commit()
        
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
    
    return '''
        <h2>Register</h2>
        <form method="post">
            <label>Username: <input type="text" name="username" required></label><br>
            <label>Password: <input type="password" name="password" required></label><br>
            <button type="submit">Register</button>
        </form>
        <p>Already have an account? <a href="/login">Log in here</a></p>
    '''

def make_space_for_new_user():
    """Deactivate oldest inactive user if we've reached the limit"""
    try:
        # Find the oldest inactive user
        inactive_threshold = datetime.utcnow() - timedelta(days=INACTIVITY_DAYS)
        oldest_inactive = User.query.filter(
            User.is_active == True,
            User.last_active < inactive_threshold
        ).order_by(User.last_active).first()
        
        if oldest_inactive:
            logger.info(f"Deactivating inactive user: {oldest_inactive.username}")
            oldest_inactive.is_active = False
            db.session.commit()
            return True
        else:
            logger.warning("No inactive users to deactivate")
            return False
            
    except Exception as e:
        logger.error(f"Error making space for new user: {str(e)}")
        return False

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password, password):
            # Update last active time
            user.last_active = datetime.utcnow()
            db.session.commit()
            
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('home'))
        else:
            flash('Invalid username or password', 'error')
    
    return '''
        <h2>Login</h2>
        <form method="post">
            <label>Username: <input type="text" name="username" required></label><br>
            <label>Password: <input type="password" name="password" required></label><br>
            <button type="submit">Log In</button>
        </form>
        <p>Don't have an account? <a href="/register">Register here</a></p>
    '''

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
def home():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    
    # Update last active time
    current_user.last_active = datetime.utcnow()
    db.session.commit()
    
    # Check if user has Strava connected
    has_strava = bool(current_user.access_token)
    
    # Get basic stats if available
    stats = {}
    if has_strava:
        try:
            # Store tokens in session for the web interface
            session['access_token'] = current_user.access_token
            session['refresh_token'] = current_user.refresh_token
            session['token_expires_at'] = current_user.token_expires_at.timestamp() if current_user.token_expires_at else None
            
            # Fetch some basic stats
            activities = get_all_activities()
            if activities:
                stats = analyze_wrapped_stats(activities)
        except Exception as e:
            logger.error(f"Error fetching Strava data: {str(e)}")
            flash('Error fetching your Strava data. Please try reconnecting.', 'error')
    
    return f"""
        <h1>Welcome, {current_user.username}!</h1>
        
        {f'<p>Last active: {current_user.last_active.strftime("%Y-%m-%d %H:%M")}</p>' if current_user.last_active else ''}
        
        <h2>Strava Connection</h2>
        {
            f'<p>Connected to Strava! <a href="/strava/disconnect">Disconnect</a></p>'
            if has_strava else
            f'<p><a href="{url_for("strava_auth")}" class="connect-strava-btn">Connect with Strava</a></p>'
        }
        
        {
            f"""
            <h2>Your Running Stats</h2>
            <div class="stats-container">
                <div class="stat-box">
                    <div class="stat-value">{stats.get('total_activities', 0)}</div>
                    <div class="stat-label">Total Runs</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value">{stats.get('total_distance_km', 0):.1f}</div>
                    <div class="stat-label">Total Distance (km)</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value">{stats.get('total_moving_time_hours', 0):.1f}</div>
                    <div class="stat-label">Total Time (hours)</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value">{stats.get('avg_pace_formatted', 'N/A')}</div>
                    <div class="stat-label">Average Pace</div>
                </div>
            </div>
            
            <h3>Recent Activities</h3>
            <ul>
                {"".join([
                    f"<li>{a.get('name', 'Unnamed Run')} - {a.get('distance', 0)/1000:.1f} km on {a.get('start_date', '')[:10]}</li>"
                    for a in activities[:5]  # Show last 5 activities
                ]) if activities else '<li>No activities found</li>'}
            </ul>
            
            <p><a href="/stats">View detailed stats</a></p>
            """ if has_strava and stats else ''
        }
        
        <style>
            .stats-container {
                display: flex;
                justify-content: space-around;
                margin: 20px 0;
                flex-wrap: wrap;
            }
            .stat-box {
                background: #f8f9fa;
                border-radius: 10px;
                padding: 15px;
                margin: 10px;
                text-align: center;
                min-width: 150px;
                box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            }
            .stat-value {
                font-size: 2rem;
                font-weight: bold;
                color: #4a5568;
            }
            .stat-label {
                color: #718096;
                font-size: 0.9rem;
            }
            .connect-strava-btn {
                display: inline-block;
                background: #FC4C02;
                color: white;
                padding: 10px 20px;
                border-radius: 5px;
                text-decoration: none;
                font-weight: 600;
                transition: background 0.3s ease;
            }
            .connect-strava-btn:hover {
                background: #e04200;
            }
        </style>
    """

@app.route('/login')
def login():
    # If user is already logged in, redirect to home
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    # Otherwise, redirect to the login page
    return redirect(url_for('login'))

@app.route('/test')
def test():
    return 'Test route is working!'

@app.route('/callback')
def callback():
    # Handle Strava OAuth callback
    error = request.args.get('error')
    if error:
        return f'Error from Strava: {error}'
    
    code = request.args.get('code')
    if not code:
        return 'No authorization code provided', 400
    
    try:
        # Exchange authorization code for access token
        response = requests.post(
            STRAVA_TOKEN_URL,
            data={
                'client_id': STRAVA_CLIENT_ID,
                'client_secret': STRAVA_CLIENT_SECRET,
                'code': code,
                'grant_type': 'authorization_code'
            }
        )
        
        if response.status_code == 200:
            token_data = response.json()
            
            # Store tokens in the database for the current user
            if current_user.is_authenticated:
                current_user.access_token = token_data['access_token']
                current_user.refresh_token = token_data['refresh_token']
                current_user.token_expires_at = datetime.fromtimestamp(token_data['expires_at'])
                current_user.last_active = datetime.utcnow()
                db.session.commit()
                
                # Also store in session for immediate use
                session['access_token'] = token_data['access_token']
                session['refresh_token'] = token_data['refresh_token']
                session['token_expires_at'] = token_data['expires_at']
                
                logger.info(f"Successfully connected Strava for user {current_user.username}")
                flash('Successfully connected to Strava!', 'success')
                return redirect(url_for('home'))
            else:
                logger.error("No authenticated user found when processing Strava callback")
                return 'User not authenticated', 401
        else:
            logger.error(f"Error exchanging code for token: {response.status_code} - {response.text}")
            return 'Failed to authenticate with Strava', 500
            
    except Exception as e:
        logger.error(f"Exception in callback: {str(e)}")
        return f'An error occurred: {str(e)}', 500

@app.route('/callback/')
def callback_with_slash():
    # Handle case where callback URL is called with a trailing slash
    return redirect(url_for('callback', **request.args))

@app.route('/logout')
def logout():
    # Clear session data
    session.pop('access_token', None)
    session.pop('refresh_token', None)
    session.pop('token_expires_at', None)
    session.pop('athlete_info', None)
    
    # Log out the user
    logout_user()
    
    # Redirect to home page
    return redirect(url_for('home'))

@app.route('/token-status')
def token_status():
    # Route to check current token status (for debugging)
    if 'access_token' not in session:
        return 'No access token in session', 401
    
    # Check if token is expired
    is_expired = time.time() >= session.get('token_expires_at', 0)
    
    return jsonify({
        'has_token': 'access_token' in session,
        'is_expired': is_expired,
        'expires_at': datetime.fromtimestamp(session.get('token_expires_at', 0)).isoformat() if 'token_expires_at' in session else None,
        'time_until_expiry': (session.get('token_expires_at', 0) - time.time()) if 'token_expires_at' in session else None
    })

@app.route('/analyze')
def analyze():
    # Route to analyze data with ChatGPT
    if 'access_token' not in session:
        return redirect(url_for('login'))
    
    try:
        # Get activities
        activities = get_all_activities()
        if not activities:
            return 'No activities found', 404
        
        # Get athlete info for the name
        athlete_name = 'Runner'  # Default name
        if 'athlete_info' in session:
            athlete_info = session['athlete_info']
            athlete_name = f"{athlete_info.get('firstname', '')} {athlete_info.get('lastname', '')}".strip() or 'Runner'
        
        # Analyze with ChatGPT
        analysis = analyze_with_chatgpt(activities, athlete_name)
        
        # Return the analysis
        return f"""
            <h1>Your Running Analysis</h1>
            <div style="white-space: pre-line; background: #f8f9fa; padding: 20px; border-radius: 5px;">
                {analysis}
            </div>
            <p><a href="/">Back to home</a></p>
        """
        
    except Exception as e:
        logger.error(f"Error in analysis: {str(e)}")
        return f'An error occurred during analysis: {str(e)}', 500

@app.route('/stats')
def get_stats_page():
    logger.info("get_stats_page called")
    
    # Check for cached stats first
    if 'cached_stats' in session and 'athlete_info' in session:
        logger.info("Using cached stats")
        stats = session['cached_stats']
        return render_template_string(STATS_TEMPLATE, **stats)
        
    try:
        # Get token from session
        if 'access_token' not in session:
            logger.warning("No access token in session, redirecting to login")
            return redirect('/login')
        
        logger.info("User has access token, fetching stats")
        athlete = session.get('athlete_info', {})
        athlete_name = str(athlete.get('firstname', 'Athlete') or 'Athlete') + ' ' + str(athlete.get('lastname', '') or '')
        logger.info(f"Generating stats page for athlete: {athlete_name}")
        logger.debug(f"athlete_name type: {type(athlete_name)}, value: {repr(athlete_name)}")
        
        logger.info("Fetching all activities")
        activities = get_all_activities()
        if activities is None:
            logger.error("Failed to fetch activities due to authentication error")
            return redirect('/login')
        logger.info(f"Fetched {len(activities)} total activities")
        
        # Filter for runs only and 2025 only
        logger.info("Filtering for 2025 runs")
        runs_2025 = [a for a in activities if a['type'] == 'Run' and a['start_date'].startswith('2025')]
        logger.info(f"Found {len(runs_2025)} runs from 2025")
        
        # Sort by date (newest first)
        runs_2025.sort(key=lambda x: x['start_date'], reverse=True)
        logger.info("Sorted runs by date (newest first)")
        
        # Create table rows for 2025 runs only - display all runs
        logger.info("Creating table rows for display")
        table_rows = ""
        max_runs = len(runs_2025)  # Display all runs
        logger.info(f"Will display all {max_runs} runs")
        
        error_count = 0
        for i, run in enumerate(runs_2025[:max_runs]):
            try:
                # Convert UTC to IST for display
                utc_date_str = run.get('start_date', 'N/A')
                if utc_date_str and utc_date_str != 'N/A':
                    utc_dt = datetime.fromisoformat(utc_date_str.replace('Z', '+00:00'))
                    ist_dt = utc_dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
                    date = ist_dt.strftime('%Y-%m-%d %H:%M IST')
                else:
                    date = 'N/A'
                
                name = run.get('name', 'N/A')
                distance = round(run.get('distance', 0) / 1000, 2)  # Convert to km
                
                # Format time
                time_sec = run.get('elapsed_time', 0)
                if time_sec > 0:
                    hours, remainder = divmod(time_sec, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    time_str = f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
                else:
                    time_str = "00:00:00"
                
                # Simple pace calculation
                if distance > 0:
                    pace_min_per_km = time_sec / 60 / distance
                    pace_min = int(pace_min_per_km)
                    pace_sec = int((pace_min_per_km - pace_min) * 60)
                    pace_str = f"{pace_min}:{pace_sec:02d}"
                else:
                    pace_str = "N/A"
                
                table_rows += f"<tr><td class='date-cell'>{date}</td><td class='activity-cell' title='{name}'>{name}</td><td class='distance-cell'>{distance}</td><td class='time-cell'>{time_str}</td><td class='pace-cell'>{pace_str}</td></tr>"
            except Exception as e:
                # Skip problematic rows but continue
                logger.error(f"Error processing run {i}: {str(e)}")
                error_count += 1
                continue
        
        # Calculate total stats
        total_runs = len(runs_2025)
        total_distance = sum(run.get('distance', 0) / 1000 for run in runs_2025)  # in km
        total_time = sum(run.get('elapsed_time', 0) / 3600 for run in runs_2025)  # in hours
        
        # Create stats HTML
        stats_html = f"""
        <div class="stats-container">
            <div class="stat-box">
                <div class="stat-value">{total_runs}</div>
                <div class="stat-label">Total Runs</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{total_distance:.1f} km</div>
                <div class="stat-label">Total Distance</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{total_time:.1f} hours</div>
                <div class="stat-label">Total Time</div>
            </div>
        </div>
        """
        
        # Prepare the template context
        context = {
            'athlete_name': athlete_name,
            'total_runs': total_runs,
            'total_distance': round(total_distance, 2),
            'total_time': round(total_time, 1),
            'table_rows': table_rows,
            'stats_html': stats_html,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # Cache the stats in the session before disconnecting
        session['cached_stats'] = context
        
        # Revoke Strava access after caching the data
        if 'access_token' in session:
            access_token = session['access_token']
            try:
                # Notify user about disconnection
                flash('Your Strava account has been disconnected. Your data is now cached for your session.')
                
                # Revoke access token with Strava
                revoke_url = 'https://www.strava.com/oauth/deauthorize'
                response = requests.post(revoke_url, data={'access_token': access_token})
                
                if response.status_code == 200:
                    logger.info("Successfully revoked Strava access")
                else:
                    logger.warning(f"Failed to revoke Strava access: {response.status_code} - {response.text}")
                
            except Exception as e:
                logger.error(f"Error revoking Strava access: {str(e)}")
            finally:
                # Always clear the tokens from session
                session.pop('access_token', None)
                session.pop('refresh_token', None)
                session.pop('token_expires_at', None)
                session.modified = True
        
        # Return the rendered template with the context
        return render_template_string(STATS_TEMPLATE, **context)
        
    except Exception as e:
        logger.error(f"Error in get_stats_page: {str(e)}")
        return f'<h1>Error</h1><p>An error occurred while processing your request: {str(e)}</p><p><a href="/">Back to home</a></p>'
