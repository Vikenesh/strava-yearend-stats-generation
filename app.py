"""
Strava Year-End Run Summary Application

This application provides a summary of Strava running activities with visualizations.
"""
import json
import logging
import os
import time
import io
import base64
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone

from flask import Flask, request, redirect, session, url_for, jsonify, send_file, Response, render_template
import requests

try:
    import pandas as pd
except Exception:
    pd = None

# Application Configuration
# ======================

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def clean_activity_data(activity):
    """Extract and clean only the minimal necessary fields from an activity.
    
    Returns:
        dict: A minimal representation of the activity with only essential fields.
        None: If the input is invalid.
    """
    if not isinstance(activity, dict):
        return None
    
    try:
        return {
            't': activity.get('type'),
            'd': activity.get('start_date', '')[:10],
            'm': float(activity.get('distance', 0)),
            's': float(activity.get('average_speed', 0)),
            'e': int(activity.get('elapsed_time', 0))
        }
    except Exception as e:
        logger.error(f"Error cleaning activity data: {str(e)}")
        return None

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'strava-stats-secret-key-2024')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)  # Session expires after 1 hour
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = True  # Requires HTTPS
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# API Configuration
# ----------------
# Strava API credentials from environment variables
CLIENT_ID = os.environ.get('STRAVA_CLIENT_ID')
CLIENT_SECRET = os.environ.get('STRAVA_CLIENT_SECRET')
REDIRECT_URI = os.environ.get('REDIRECT_URI', 'https://strava-year-end-summary-production.up.railway.app/callback')

# xAI Grok API configuration
XAI_API_KEY = os.environ.get('XAI_API_KEY')
XAI_API_URL = "https://api.x.ai/v1/chat/completions"

# Warn on startup if credentials are missing
if not CLIENT_ID or not CLIENT_SECRET:
    logger.warning('STRAVA_CLIENT_ID or STRAVA_CLIENT_SECRET not set. Ensure Railway shared variables are configured.')
else:
    logger.info('STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET found in environment')

if not XAI_API_KEY:
    logger.warning('XAI_API_KEY not set. AI poster generation will not work.')
else:
    logger.info('XAI_API_KEY present: True')

# Helper Functions
# ===============

def build_authorization_url():
    """Build the Strava OAuth authorization URL."""
    params = {
        'client_id': CLIENT_ID,
        'redirect_uri': REDIRECT_URI,
        'response_type': 'code',
        'scope': 'read,activity:read_all,profile:read_all',
        'approval_prompt': 'force'
    }
    query = '&'.join([f"{k}={v}" for k, v in params.items()])
    return f"https://www.strava.com/oauth/authorize?{query}"

def refresh_access_token():
    """Refresh the Strava access token using the refresh token."""
    if 'refresh_token' not in session:
        logger.warning("No refresh token available")
        return False
    
    token_url = 'https://www.strava.com/oauth/token'
    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'grant_type': 'refresh_token',
        'refresh_token': session['refresh_token']
    }
    
    try:
        response = requests.post(token_url, data=data)
        if response.status_code == 200:
            token_response = response.json()
            session.update({
                'access_token': token_response['access_token'],
                'refresh_token': token_response.get('refresh_token', session['refresh_token']),
                'token_expires_at': time.time() + token_response.get('expires_in', 21600)
            })
            logger.info("Token refreshed successfully")
            return True
        else:
            logger.error(f"Failed to refresh token: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error refreshing token: {str(e)}")
        return False

def get_valid_access_token():
    """Get a valid access token, refreshing if necessary."""
    if 'access_token' not in session:
        return None
    
    expires_at = session.get('token_expires_at', 0)
    if time.time() >= (expires_at - 300):  # Refresh if token expires in less than 5 minutes
        logger.info("Access token expired or about to expire, refreshing...")
        if not refresh_access_token():
            return None
    
    return session['access_token']

def get_all_activities():
    """Fetch all activities from Strava API with pagination."""
    if 'access_token' not in session:
        logger.warning("No access token in session")
        return None
    
    all_activities = []
    page = 1
    per_page = 200  # Maximum allowed by Strava API
    access_token = get_valid_access_token()
    
    if not access_token:
        logger.error("No valid access token available")
        return None
    
    try:
        while True:
            # Make request to Strava API
            headers = {'Authorization': f'Bearer {access_token}'}
            params = {'per_page': per_page, 'page': page}
            
            response = requests.get(
                'https://www.strava.com/api/v3/athlete/activities',
                headers=headers,
                params=params
            )
            
            # Handle rate limiting
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 60))
                logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
                time.sleep(retry_after)
                continue
                
            if response.status_code != 200:
                logger.error(f"Error fetching activities: {response.status_code} - {response.text}")
                return None
                
            activities = response.json()
            if not activities:
                break
                
            all_activities.extend(activities)
            logger.info(f"Fetched page {page} with {len(activities)} activities")
            page += 1
            
            # Small delay to avoid hitting rate limits
            time.sleep(1)
            
    except Exception as e:
        logger.error(f"Error in get_all_activities: {str(e)}")
        return None
        
    logger.info(f"Total activities fetched: {len(all_activities)}")
    return all_activities

# Routes
# ======

@app.route('/')
def index():
    """Main route - shows login or dashboard based on authentication status."""
    logger.info("Index route accessed")
    
    if 'access_token' in session:
        logger.info("User is authenticated, redirecting to stats")
        return redirect('/stats')
    else:
        logger.info("User not authenticated, showing login page")
        auth_url = build_authorization_url()
        return render_template('index.html', auth_url=auth_url)

@app.route('/login')
def login():
    """Redirect to Strava OAuth authorization."""
    logger.info("Login route accessed")
    auth_url = build_authorization_url()
    return redirect(auth_url)

@app.route('/callback')
def callback():
    """Handle OAuth callback from Strava."""
    error = request.args.get('error')
    if error:
        return f'<h1>Authorization Error</h1><p>{request.args.get("error_description")}</p>'
    
    code = request.args.get('code')
    if not code:
        return '<h1>Error</h1><p>No authorization code provided</p>'
    
    # Exchange code for token
    token_url = 'https://www.strava.com/oauth/token'
    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'code': code,
        'grant_type': 'authorization_code'
    }
    
    try:
        response = requests.post(token_url, data=data)
        if response.status_code != 200:
            logger.error(f"Failed to exchange code for token: {response.status_code} - {response.text}")
            return f'<h1>Error</h1><p>Failed to exchange code for token: {response.text}</p>'
        
        token_response = response.json()
        athlete_info = token_response.get('athlete', {})
        
        # Store only essential data in session
        session.update({
            'access_token': token_response['access_token'],
            'refresh_token': token_response.get('refresh_token'),
            'token_expires_at': time.time() + token_response.get('expires_in', 21600),
            'athlete_info': {
                'id': athlete_info.get('id'),
                'firstname': athlete_info.get('firstname', 'Runner'),
                'lastname': athlete_info.get('lastname', ''),
                'profile_medium': athlete_info.get('profile_medium', '')
            }
        })
        
        logger.info(f"User {athlete_info.get('firstname')} {athlete_info.get('lastname')} logged in successfully")
        return redirect('/stats')
        
    except Exception as e:
        logger.error(f"Error in callback: {str(e)}")
        return f'<h1>Error</h1><p>An error occurred during authentication: {str(e)}</p>'

@app.route('/stats')
def stats():
    """Show the main stats dashboard."""
    if 'access_token' not in session:
        return redirect('/')
    
    # Get activities and process them
    activities = get_all_activities()
    if activities is None:
        return '<h1>Error</h1><p>Failed to fetch activities</p>'
    
    # Process activities for the current year
    current_year = datetime.now().year
    runs = [
        clean_activity_data(a) 
        for a in activities 
        if a.get('type') == 'Run' and str(current_year) in a.get('start_date', '')
    ]
    runs = [r for r in runs if r is not None]
    
    # Calculate stats
    total_distance = round(sum(r.get('m', 0) / 1000 for r in runs), 1)  # Convert to km
    total_time = round(sum(r.get('e', 0) / 3600 for r in runs), 1)  # Convert to hours
    
    # Get athlete info
    athlete = session.get('athlete_info', {})
    athlete_name = f"{athlete.get('firstname', 'Runner')} {athlete.get('lastname', '')}".strip()
    
    return render_template('stats.html',
                         athlete_name=athlete_name,
                         total_runs=len(runs),
                         total_distance=total_distance,
                         total_time=total_time,
                         year=current_year)

@app.route('/generate-grok-poster', methods=['POST'])
def generate_grok_poster_route():
    """API endpoint to generate a poster using xAI Grok model."""
    if 'access_token' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        current_year = datetime.now().year
        athlete = session.get('athlete_info', {})
        athlete_name = f"{athlete.get('firstname', 'Runner')} {athlete.get('lastname', '')}".strip()
        
        # Fetch activities
        activities = get_all_activities()
        if not activities:
            return jsonify({'error': 'No activities found'}), 404
        
        # Filter runs for current year
        runs = [
            clean_activity_data(a) 
            for a in activities 
            if a.get('type') == 'Run' and str(current_year) in a.get('start_date', '')
        ]
        runs = [r for r in runs if r is not None]
        
        if not runs:
            return jsonify({'error': f'No runs found for {current_year}'}), 404
        
        # Generate poster HTML using xAI Grok
        poster_html = generate_grok_poster(runs, athlete_name)
        if not poster_html:
            return jsonify({'error': 'Failed to generate poster'}), 500
        
        # Calculate stats for the response
        total_distance = round(sum(r.get('m', 0) / 1000 for r in runs), 1)
        total_time = round(sum(r.get('e', 0) / 3600 for r in runs), 1)
        
        return jsonify({
            'success': True,
            'poster_html': poster_html,
            'stats': {
                'athlete_name': athlete_name,
                'total_runs': len(runs),
                'total_distance_km': total_distance,
                'total_time_hours': total_time,
                'year': current_year
            }
        })
        
    except Exception as e:
        logger.error(f"Error in generate_grok_poster_route: {str(e)}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

def generate_grok_poster(activities, athlete_name):
    """Generate a poster using xAI Grok model with running stats."""
    if not XAI_API_KEY:
        logger.error("XAI_API_KEY not configured")
        return None
    
    try:
        # Prepare the prompt with activity data
        prompt = f"""
        Create an HTML+CSS poster for a runner's year in review with the following details:
        
        - Athlete: {athlete_name}
        - Total Runs: {len(activities)}
        - Total Distance: {sum(a.get('m', 0) / 1000 for a in activities):.1f} km
        - Total Time: {sum(a.get('e', 0) / 3600 for a in activities):.1f} hours
        
        The poster should be visually appealing and include:
        1. A creative title
        2. Key statistics
        3. A motivational message
        4. A clean, modern design
        
        Return only the HTML+CSS, no markdown or code block formatting.
        """
        
        headers = {
            "Authorization": f"Bearer {XAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "grok-1",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant that creates beautiful HTML+CSS posters for runners."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 2000
        }
        
        response = requests.post(XAI_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        
        result = response.json()
        poster_html = result['choices'][0]['message']['content']
        
        # Clean up the response if it's wrapped in markdown code blocks
        if '```html' in poster_html:
            poster_html = poster_html.split('```html')[1].split('```')[0].strip()
        elif '```' in poster_html:
            poster_html = poster_html.split('```')[1].strip()
        
        return poster_html
        
    except Exception as e:
        logger.error(f"Error generating xAI Grok poster: {str(e)}")
        return None

@app.route('/logout')
def logout():
    """Log out the user by clearing the session."""
    session.clear()
    return redirect('/')

# Run the application
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
