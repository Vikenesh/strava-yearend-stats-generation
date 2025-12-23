import os
import requests
import json
import logging
from flask import Flask, request, redirect, session, url_for
from collections import defaultdict, Counter
import calendar
import time
from datetime import datetime, timedelta, timezone

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'strava-stats-secret-key-2024')

# Strava API credentials from environment variables
CLIENT_ID = os.environ.get('STRAVA_CLIENT_ID', '130483')
CLIENT_SECRET = os.environ.get('STRAVA_CLIENT_SECRET', '71fc47a3e9e1c93e165ae106ca532d1bc428088e')
REDIRECT_URI = 'https://strava-year-end-summary-production.up.railway.app/callback'

# OpenAI API settings
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', 'your-openai-api-key-here')

def utc_to_ist(utc_datetime_str):
    """Convert UTC datetime string to IST timezone"""
    utc_dt = datetime.fromisoformat(utc_datetime_str.replace('Z', '+00:00'))
    ist_dt = utc_dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
    return ist_dt

def analyze_wrapped_stats(activities):
    """Analyze activities for wrapped-style visualization"""
    logger.info("analyze_wrapped_stats called")
    runs = [a for a in activities if a['type'] == 'Run']
    
    if not runs:
        logger.info("No runs found")
        return None
    
    # Convert all dates to IST
    ist_runs = []
    for run in runs:
        ist_dt = utc_to_ist(run['start_date'])
        ist_run = run.copy()
        ist_run['ist_date'] = ist_dt
        ist_runs.append(ist_run)
    
    # Basic stats
    total_distance = sum(run['distance'] for run in ist_runs) / 1000  # km
    total_time = sum(run['moving_time'] for run in ist_runs)  # seconds
    total_activities = len(ist_runs)
    
    # Monthly breakdown
    monthly_stats = defaultdict(lambda: {'distance': 0, 'count': 0, 'time': 0})
    for run in ist_runs:
        month_key = run['ist_date'].strftime('%Y-%m')
        monthly_stats[month_key]['distance'] += run['distance'] / 1000
        monthly_stats[month_key]['count'] += 1
        monthly_stats[month_key]['time'] += run['moving_time']
    
    # Fastest/Longest activities
    fastest_run = min(ist_runs, key=lambda x: x['moving_time'] / (x['distance'] / 1000) if x['distance'] > 0 else float('inf'))
    longest_run = max(ist_runs, key=lambda x: x['distance'])
    
    # Time patterns
    early_morning_runs = [r for r in ist_runs if 5 <= r['ist_date'].hour < 9]
    night_runs = [r for r in ist_runs if 20 <= r['ist_date'].hour or r['ist_date'].hour < 5]
    
    # Consistency streaks
    dates = sorted(set(run['ist_date'].date() for run in ist_runs))
    current_streak = 0
    max_streak = 0
    temp_streak = 0
    
    for i in range(len(dates)):
        if i == 0:
            temp_streak = 1
        elif (dates[i] - dates[i-1]).days == 1:
            temp_streak += 1
        else:
            max_streak = max(max_streak, temp_streak)
            temp_streak = 1
        max_streak = max(max_streak, temp_streak)
    
    # Check if current streak continues to today
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    if dates and (today - dates[-1]).days <= 1:
        current_streak = temp_streak
    
    # Favorite day of week
    day_counts = Counter(run['ist_date'].strftime('%A') for run in ist_runs)
    favorite_day = day_counts.most_common(1)[0] if day_counts else ('None', 0)
    
    logger.info("Analysis completed")
    return {
        'total_distance': round(total_distance, 2),
        'total_time_hours': round(total_time / 3600, 1),
        'total_activities': total_activities,
        'monthly_stats': dict(monthly_stats),
        'fastest_run': {
            'name': fastest_run['name'],
            'pace': round((fastest_run['moving_time'] / 60) / (fastest_run['distance'] / 1000), 2),
            'date': fastest_run['ist_date'].strftime('%d %b %Y, %I:%M %p IST'),
            'distance': round(fastest_run['distance'] / 1000, 2)
        },
        'longest_run': {
            'name': longest_run['name'],
            'distance': round(longest_run['distance'] / 1000, 2),
            'date': longest_run['ist_date'].strftime('%d %b %Y, %I:%M %p IST'),
            'time': longest_run['moving_time'] // 60
        },
        'early_bird_count': len(early_morning_runs),
        'night_owl_count': len(night_runs),
        'current_streak': current_streak,
        'max_streak': max_streak,
        'favorite_day': favorite_day,
        'avg_pace': round((total_time / 60) / total_distance, 2) if total_distance > 0 else 0,
        'ist_runs': ist_runs
    }

def refresh_access_token():
    """Refresh the access token using the refresh token"""
    logger.info("Attempting to refresh access token")
    
    refresh_token = session.get('refresh_token')
    if not refresh_token:
        logger.error("No refresh token available")
        return False
    
    token_data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token
    }
    
    try:
        response = requests.post('https://www.strava.com/oauth/token', data=token_data)
        logger.info(f"Token refresh response status: {response.status_code}")
        
        if response.status_code != 200:
            logger.error(f"Token refresh failed: {response.text}")
            return False
        
        token_response = response.json()
        session['access_token'] = token_response['access_token']
        session['refresh_token'] = token_response.get('refresh_token', refresh_token)  # Update if new one provided
        session['token_expires_at'] = time.time() + token_response.get('expires_in', 21600)
        
        logger.info("Token refreshed successfully")
        logger.info(f"New token expires at: {datetime.fromtimestamp(session['token_expires_at'])}")
        return True
        
    except Exception as e:
        logger.error(f"Error during token refresh: {str(e)}")
        return False

def revoke_strava_access():
    """Revoke the current user's access to Strava"""
    if 'access_token' not in session:
        return False
        
    try:
        # Try to refresh the token first to ensure it's valid
        token = get_valid_access_token()
        if not token:
            logger.warning("Cannot get valid access token for deauthorization")
            # Token is already invalid/expired, so we're effectively deauthorized
            return True
        
        # Revoke access using Strava's deauthorization endpoint
        response = requests.post('https://www.strava.com/oauth/deauthorize', data={
            'access_token': token
        })
        
        logger.info(f"Strava deauthorize response: {response.status_code} - {response.text}")
        
        # Check if deauthorization was successful
        if response.status_code == 200:
            logger.info("Successfully deauthorized athlete from Strava")
            return True
        elif response.status_code == 401:
            # Token is already invalid, which means we're effectively deauthorized
            logger.info("Token already invalid - athlete is effectively deauthorized")
            return True
        else:
            logger.error(f"Failed to deauthorize. Status: {response.status_code}, Response: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error revoking Strava access: {str(e)}")
        return False


def get_valid_access_token():
    """Get a valid access token, refreshing if necessary"""
    # Check if we have a token
    if 'access_token' not in session:
        logger.warning("No access token in session")
        return None
    
    # Check if token is still valid (with 5-minute buffer)
    expires_at = session.get('token_expires_at', 0)
    if time.time() < expires_at - 300:  # 5 minute buffer
        logger.debug("Access token is still valid")
        return session['access_token']
    
    # Token is expired, try to refresh
    logger.info("Access token expired, attempting refresh")
    if refresh_access_token():
        return session['access_token']
    else:
        logger.error("Failed to refresh token, user needs to re-authenticate")
        return None

def get_all_activities():
    logger.info("get_all_activities called")
    
    # Get valid token (will refresh if needed)
    token = get_valid_access_token()
    if not token:
        logger.error("No valid access token available")
        return None
    
    headers = {'Authorization': f'Bearer {token}'}
    all_activities = []
    page = 1
    
    logger.info("Fetching activities...")
    while True:
        url = f'https://www.strava.com/api/v3/athlete/activities?page={page}&per_page=200'
        logger.debug(f"Fetching page {page}")
        response = requests.get(url, headers=headers)
        
        # Handle 401/403 errors - try token refresh
        if response.status_code in [401, 403]:
            logger.warning(f"Authentication error ({response.status_code}), attempting token refresh")
            if refresh_access_token():
                token = session['access_token']
                headers = {'Authorization': f'Bearer {token}'}
                response = requests.get(url, headers=headers)
            else:
                logger.error("Token refresh failed, cannot fetch activities")
                return None
        
        if response.status_code != 200:
            logger.error(f"Error fetching page {page}: {response.status_code}")
            break
            
        try:
            data = response.json()
        except Exception as e:
            logger.error(f"Error parsing JSON: {e}")
            break
        
        if not data:  # No more activities
            logger.info(f"Fetched {len(all_activities)} total activities from {page-1} pages")
            break
            
        all_activities.extend(data)
        logger.info(f"Fetched page {page}, got {len(data)} activities, total so far: {len(all_activities)}")
        page += 1
        
        # Safety check to prevent infinite loops
        if page > 10:
            logger.warning("Safety limit reached, stopping fetch")
            break
    
    logger.info(f"Total activities fetched: {len(all_activities)}")
    return all_activities

def analyze_with_chatgpt(activities, athlete_name):
    """Analyze activities using ChatGPT API"""
    logger.info(f"analyze_with_chatgpt called for {len(activities)} activities")
    try:
        # Filter for 2025 runs only
        runs_2025 = [a for a in activities if a['type'] == 'Run' and a['start_date'].startswith('2025')]
        logger.info(f"Processing {len(runs_2025)} runs from 2025")
        
        # Prepare data for ChatGPT
        summary = {
            'athlete': athlete_name,
            'total_runs': len(runs_2025),
            'total_distance': round(sum(a['distance'] / 1000 for a in runs_2025), 2),
            'recent_runs': []
        }
        
        # Add recent 10 runs for detailed analysis
        for run in runs_2025[:10]:
            summary['recent_runs'].append({
                'date': run['start_date'][:10],
                'name': run['name'],
                'distance_km': round(run['distance'] / 1000, 2),
                'time_minutes': run['moving_time'] // 60,
                'pace_min_per_km': round((run['moving_time'] / 60) / (run['distance'] / 1000), 2)
            })
        
        # Create prompt for ChatGPT
        prompt = f"""
        Analyze this 2025 running data for {athlete_name}:
        
        Summary: {json.dumps(summary, indent=2)}
        
        Please provide:
        1. Performance insights and trends
        2. Training recommendations
        3. Goal setting suggestions
        4. Notable achievements
        5. Areas for improvement
        
        Format the response in a clear, encouraging way suitable for an athlete.
        """
        
        try:
            headers = {
                'Authorization': f'Bearer {OPENAI_API_KEY}',
                'Content-Type': 'application/json'
            }
            
            data = {
                'model': 'gpt-3.5-turbo',
                'messages': [
                    {'role': 'system', 'content': 'You are a helpful running coach and data analyst.'},
                    {'role': 'user', 'content': prompt}
                ],
                'max_tokens': 1000,
                'temperature': 0.7
            }
            
            response = requests.post('https://api.openai.com/v1/chat/completions', headers=headers, json=data)
            logger.info(f"OpenAI API response status: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()['choices'][0]['message']['content']
                logger.info("Successfully received analysis from OpenAI")
                return result
            else:
                logger.error(f"OpenAI API error: {response.status_code} - {response.text}")
                return f"OpenAI API Error: {response.status_code} - {response.text}"
                
        except Exception as e:
            logger.error(f"Error calling OpenAI API: {str(e)}")
            return f"Error calling OpenAI API: {str(e)}"
            
    except Exception as e:
        logger.error(f"Error in analyze_with_chatgpt: {str(e)}")
        return f"Error in analyze_with_chatgpt: {str(e)}"

@app.route('/')
def index():
    logger.info("Index route accessed")
    logger.debug(f"Session keys: {list(session.keys())}")
    
    if 'access_token' in session:
        logger.info("User is logged in, showing stats page")
        return get_stats_page()
    else:
        logger.info("User not logged in, showing login page")
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Year-End Running Summary for Strava - 2025</title>
            <style>
                * {{
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                }}
                
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    min-height: 100vh;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    overflow: hidden;
                }}
                
                .container {{
                    text-align: center;
                    padding: 2rem;
                    max-width: 600px;
                    position: relative;
                }}
                
                .year-display {{
                    font-size: 4rem;
                    font-weight: bold;
                    color: #ffffff;
                    text-shadow: 3px 3px 6px rgba(0,0,0,0.3);
                    margin-bottom: 1rem;
                    animation: glow 2s ease-in-out infinite alternate;
                }}
                
                @keyframes glow {{
                    from {{ text-shadow: 3px 3px 6px rgba(0,0,0,0.3); }}
                    to {{ text-shadow: 3px 3px 20px rgba(255,255,255,0.5); }}
                }}
                
                .title {{
                    font-size: 2.5rem;
                    color: #ffffff;
                    margin-bottom: 2rem;
                    font-weight: 300;
                }}
                
                .subtitle {{
                    font-size: 1.2rem;
                    color: #e0e0e0;
                    margin-bottom: 3rem;
                    line-height: 1.6;
                }}
                
                .login-btn {{
                    display: inline-block;
                    background: linear-gradient(45deg, #ff6b6b, #ee5a52);
                    color: white;
                    padding: 1rem 2.5rem;
                    text-decoration: none;
                    border-radius: 50px;
                    font-size: 1.1rem;
                    font-weight: 600;
                    transition: all 0.3s ease;
                    box-shadow: 0 4px 15px rgba(238, 82, 83, 0.4);
                }}
                
                .login-btn:hover {{
                    transform: translateY(-3px);
                    box-shadow: 0 8px 25px rgba(238, 82, 83, 0.6);
                    background: linear-gradient(45deg, #ee5a52, #ff6b6b);
                }}
                
                .calendar-icon {{
                    font-size: 3rem;
                    margin-bottom: 1rem;
                    animation: spin 20s linear infinite;
                }}
                
                @keyframes spin {{
                    from {{ transform: rotate(0deg); }}
                    to {{ transform: rotate(360deg); }}
                }}
                
                .confetti {{
                    position: absolute;
                    width: 10px;
                    height: 10px;
                    background: #ff6b6b;
                    animation: fall 3s linear infinite;
                }}
                
                .confetti:nth-child(1) {{ left: 10%; animation-delay: 0s; background: #ff6b6b; }}
                .confetti:nth-child(2) {{ left: 20%; animation-delay: 0.5s; background: #4ecdc4; }}
                .confetti:nth-child(3) {{ left: 30%; animation-delay: 1s; background: #45b7d1; }}
                .confetti:nth-child(4) {{ left: 40%; animation-delay: 1.5s; background: #f9ca24; }}
                .confetti:nth-child(5) {{ left: 50%; animation-delay: 2s; background: #f4d03f; }}
                .confetti:nth-child(6) {{ left: 60%; animation-delay: 2.5s; background: #6c5ce7; }}
                .confetti:nth-child(7) {{ left: 70%; animation-delay: 0.3s; background: #a8e6cf; }}
                .confetti:nth-child(8) {{ left: 80%; animation-delay: 0.8s; background: #ffd700; }}
                .confetti:nth-child(9) {{ left: 90%; animation-delay: 1.3s; background: #ff69b4; }}
                
                @keyframes fall {{
                    0% {{ transform: translateY(-100vh) rotate(0deg); opacity: 1; }}
                    100% {{ transform: translateY(100vh) rotate(360deg); opacity: 0; }}
                }}
                
                .features {{
                    margin-top: 2rem;
                    color: #e0e0e0;
                }}
                
                .feature {{
                    margin: 0.5rem 0;
                    font-size: 0.9rem;
                }}
            </style>
        </head>
        <body>
            <div class="confetti"></div>
            <div class="confetti"></div>
            <div class="confetti"></div>
            <div class="confetti"></div>
            <div class="confetti"></div>
            <div class="confetti"></div>
            <div class="confetti"></div>
            <div class="confetti"></div>
            <div class="confetti"></div>
            
            <div class="container">
                <div class="calendar-icon">📅</div>
                <div class="year-display">2025</div>
                <h1 class="title">Year-End Running Summary</h1>
                <p class="subtitle">
                    Celebrate your 2025 running journey with personalized insights,<br>
                    AI-powered analysis, and shareable achievements
                </p>
                
                <a href="/login" class="login-btn">
                    🏃‍♂️ Connect with Strava
                </a>
                
                <div class="features">
                    <div class="feature">📊 Detailed Statistics & Analytics</div>
                    <div class="feature">🤖 AI-Powered Insights</div>
                    <div class="feature">📱 Social Media Ready</div>
                    <div class="feature">🎯 Goal Tracking</div>
                    <div class="feature">🏆 Achievement Badges</div>
                </div>
            </div>
        </body>
        </html>
        """

@app.route('/login')
def login():
    logger.info("Login route accessed")
    logger.debug(f"CLIENT_ID = {CLIENT_ID}")
    logger.debug(f"REDIRECT_URI = {REDIRECT_URI}")
    auth_url = f'https://www.strava.com/oauth/authorize?client_id={CLIENT_ID}&response_type=code&redirect_uri={REDIRECT_URI}&scope=read,activity:read_all,profile:read_all&approval_prompt=force'
    logger.debug(f"Full auth URL = {auth_url}")
    logger.info("Redirecting to Strava OAuth...")
    return redirect(auth_url)

@app.route('/test')
def test():
    return "Test route is working!"

@app.route('/callback')
def callback():
    logger.info("Callback route accessed")
    logger.debug(f"Request args: {dict(request.args)}")
    
    code = request.args.get('code')
    error = request.args.get('error')
    
    if error:
        logger.error(f"OAuth error: {error}")
        return f'<h1>OAuth Error</h1><p>Error: {error}</p><p><a href="/">Back to home</a></p>'
    
    if not code:
        logger.error("No authorization code received")
        return '<h1>Error</h1><p>No authorization code received</p><p><a href="/">Back to home</a></p>'
    
    logger.info(f"Received authorization code: {code[:10]}...")
    
    token_data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'code': code,
        'grant_type': 'authorization_code'
    }
    
    logger.info("Requesting access token...")
    response = requests.post('https://www.strava.com/oauth/token', data=token_data)
    
    logger.info(f"Token response status: {response.status_code}")
    logger.debug(f"Token response: {response.text[:200]}...")
    
    if response.status_code != 200:
        logger.error("Token exchange failed")
        return f'<h1>Error</h1><p>Failed to exchange code for token: {response.text}</p><p><a href="/">Back to home</a></p>'
    
    token_response = response.json()
    session['access_token'] = token_response['access_token']
    session['refresh_token'] = token_response.get('refresh_token')
    session['token_expires_at'] = time.time() + token_response.get('expires_in', 21600)  # Default 6 hours
    session['athlete_info'] = token_response.get('athlete', {})
    
    logger.info("Successfully obtained access token")
    logger.info(f"Token expires at: {datetime.fromtimestamp(session['token_expires_at'])}")
    logger.info(f"Refresh token available: {'Yes' if session.get('refresh_token') else 'No'}")
    logger.info(f"Athlete info: {session['athlete_info'].get('firstname', 'Unknown')} {session['athlete_info'].get('lastname', '')}")
    
    return redirect('/')

@app.route('/callback/')
def callback_with_slash():
    logger.info("Callback with slash route accessed")
    return "Callback with slash works!"

@app.route('/logout')
def logout():
    """Log out the user, revoke access, and clear session data."""
    try:
        # Try to revoke access if token exists
        if 'access_token' in session:
            # Get the access token before clearing the session
            access_token = session['access_token']
            
            # Make the deauthorization request with required parameters
            response = requests.post('https://www.strava.com/oauth/deauthorize', data={
                'access_token': access_token,
                'client_id': CLIENT_ID,
                'client_secret': CLIENT_SECRET
            })
            
            if response.status_code == 200:
                logger.info("Successfully revoked Strava access")
            else:
                logger.warning(f"Failed to revoke access. Status: {response.status_code}, Response: {response.text}")
                
    except Exception as e:
        logger.error(f"Error during logout: {str(e)}")
    
    # Clear the session regardless of revoke success/failure
    session.clear()
    logger.info("User session cleared")
    return redirect(url_for('index'))

@app.route('/test-deauth')
def test_deauth():
    """Test route to check deauthorization endpoint"""
    if 'access_token' not in session:
        return '<h1>No Token</h1><p>Please login first. <a href="/login">Login</a></p>'
    
    try:
        # Test the deauthorization endpoint
        response = requests.post('https://www.strava.com/oauth/deauthorize', data={
            'access_token': session['access_token']
        })
        
        return f"""
        <h1>Deauthorization Test</h1>
        <p><strong>Status Code:</strong> {response.status_code}</p>
        <p><strong>Response:</strong></p>
        <pre>{response.text}</pre>
        <p><a href="/">Back to Stats</a> | <a href="/logout">Logout</a></p>
        """
    except Exception as e:
        return f'<h1>Error</h1><p>{str(e)}</p><p><a href="/">Back to stats</a></p>'

@app.route('/token-status')
def token_status():
    """Route to check current token status (for debugging)"""
    if 'access_token' not in session:
        return '<h1>No Token</h1><p>No access token in session. <a href="/login">Login</a></p>'
    
    expires_at = session.get('token_expires_at', 0)
    time_remaining = expires_at - time.time()
    refresh_available = 'Yes' if session.get('refresh_token') else 'No'
    
    status_html = f"""
    <h1>Token Status</h1>
    <p><strong>Access Token:</strong> Available</p>
    <p><strong>Expires At:</strong> {datetime.fromtimestamp(expires_at).strftime('%Y-%m-%d %H:%M:%S')}</p>
    <p><strong>Time Remaining:</strong> {int(time_remaining // 60)} minutes {int(time_remaining % 60)} seconds</p>
    <p><strong>Refresh Token Available:</strong> {refresh_available}</p>
    <p><strong>Athlete:</strong> {session.get('athlete_info', {}).get('firstname', 'Unknown')} {session.get('athlete_info', {}).get('lastname', '')}</p>
    <p><a href="/">Back to Stats</a> | <a href="/logout">Logout</a></p>
    """
    
    return status_html

@app.route('/analyze')
def analyze():
    """Route to analyze data with ChatGPT"""
    logger.info("Analyze route accessed")
    try:
        if 'access_token' not in session:
            logger.warning("No access token in session, redirecting to login")
            return redirect('/login')
        
        logger.info("User has access token, proceeding with analysis")
        athlete = session.get('athlete_info', {})
        athlete_name = str(athlete.get('firstname', 'Athlete') or 'Athlete') + ' ' + str(athlete.get('lastname', '') or '')
        logger.info(f"Analyzing data for athlete: {athlete_name}")
        
        logger.info("Fetching activities for analysis")
        activities = get_all_activities()
        if activities is None:
            logger.error("Failed to fetch activities due to authentication error")
            return redirect('/login')
        logger.info(f"Fetched {len(activities)} total activities for analysis")
        
        logger.info("Calling ChatGPT API for analysis")
        analysis = analyze_with_chatgpt(activities, athlete_name)
        logger.info("ChatGPT analysis completed")
        
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>ChatGPT Analysis</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .analysis {{ background: #f0f8ff; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                .back-btn {{ background: #007bff; color: white; padding: 10px; text-decoration: none; border-radius: 4px; }}
            </style>
        </head>
        <body>
            <h1>ChatGPT Running Analysis for {athlete_name}</h1>
            <a href="/" class="back-btn">← Back to Stats</a>
            
            <div class="analysis">
                <h2>AI Coach Insights</h2>
                <pre style="white-space: pre-wrap; font-family: Arial;">{analysis}</pre>
            </div>
            
            <p><a href="/" class="back-btn">← Back to Stats</a></p>
        </body>
        </html>
        """
    except Exception as e:
        logger.error(f"Error in analyze route: {str(e)}")
        return f'<h1>Error</h1><p>{str(e)}</p><p><a href="/">Back to stats</a></p>'

def get_stats_page():
    logger.info("get_stats_page called")
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
        
        # Revoke access after successfully fetching data
        logger.info("Revoking Strava access after fetching data")
        revoke_strava_access()
        
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
                
                name = run.get('name', 'Unknown Activity')
                distance = round(float(run.get('distance', 0)) / 1000, 2)  # Convert to km
                time_sec = int(run.get('moving_time', 0))
                
                # Simple time formatting
                if time_sec > 0:
                    hours = time_sec // 3600
                    minutes = (time_sec % 3600) // 60
                    seconds = time_sec % 60
                    time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
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
                table_rows += f"<tr><td>Error</td><td>Error in data</td><td>-</td><td>-</td><td>-</td></tr>"
                continue
        
        logger.info(f"Table generation completed with {error_count} errors")
        
        # Ensure athlete_name is a clean string for template
        athlete_name_display = str(athlete_name).strip()
        logger.debug(f"athlete_name_display: {repr(athlete_name_display)}")
        
        # Pre-calculate template variables to avoid function call issues
        total_activities_count = len(activities)
        runs_2025_count = len(runs_2025)
        other_activities_count = total_activities_count - len([a for a in activities if a['type'] == 'Run'])
        display_info = f'<p><em>Displaying all {runs_2025_count} runs from 2025</em></p>'
        
        # Pre-generate CSV data for JavaScript
        csv_data = 'Date,Activity,Distance (km),Time,Pace (min/km)\\n'
        for run in runs_2025[:max_runs]:
            try:
                # Convert UTC to IST for CSV
                utc_date_str = run.get('start_date', 'N/A')
                if utc_date_str and utc_date_str != 'N/A':
                    utc_dt = datetime.fromisoformat(utc_date_str.replace('Z', '+00:00'))
                    ist_dt = utc_dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
                    date = ist_dt.strftime('%Y-%m-%d %H:%M IST')
                else:
                    date = 'N/A'
                
                name = run.get('name', 'Unknown Activity').replace(',', ';')  # Replace commas to avoid CSV issues
                distance = round(float(run.get('distance', 0)) / 1000, 2)
                time_sec = int(run.get('moving_time', 0))
                
                if time_sec > 0:
                    hours = time_sec // 3600
                    minutes = (time_sec % 3600) // 60
                    seconds = time_sec % 60
                    time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                else:
                    time_str = "00:00:00"
                
                if distance > 0:
                    pace_min_per_km = time_sec / 60 / distance
                    pace_min = int(pace_min_per_km)
                    pace_sec = int((pace_min_per_km - pace_min) * 60)
                    pace_str = f"{pace_min}:{pace_sec:02d}"
                else:
                    pace_str = "N/A"
                
                csv_data += f"{date},{name},{distance},{time_str},{pace_str}\\n"
            except Exception as e:
                continue
        
        logger.debug(f"Template vars - total_activities: {total_activities_count}, runs_2025: {runs_2025_count}, other: {other_activities_count}")
        logger.debug(f"Generated CSV data with {len(csv_data.split(chr(10)))} lines")
        
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Your 2025 Year-End Running Summary for Strava</title>
            <style>
                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                
                body {{ 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    min-height: 100vh;
                    padding: 20px;
                }}
                
                .container {{ 
                    max-width: 1400px; 
                    margin: 0 auto; 
                    background: rgba(255, 255, 255, 0.95);
                    border-radius: 20px;
                    box-shadow: 0 20px 40px rgba(0,0,0,0.1);
                    overflow: hidden;
                }}
                
                .header {{ 
                    background: linear-gradient(135deg, #FC4C02 0%, #ff6b35 100%);
                    color: white;
                    padding: 30px;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    flex-wrap: wrap;
                    gap: 20px;
                }}
                
                .title {{ 
                    font-size: 2.5rem; 
                    font-weight: 700;
                    text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
                }}
                
                .stats {{ 
                    background: rgba(255,255,255,0.1);
                    padding: 20px;
                    border-radius: 15px;
                    backdrop-filter: blur(10px);
                }}
                
                .stats p {{ 
                    margin: 8px 0; 
                    font-size: 1.1rem;
                    font-weight: 500;
                }}
                
                .button-container {{ 
                    padding: 30px;
                    background: #f8f9fa;
                    display: flex;
                    gap: 15px;
                    flex-wrap: wrap;
                    justify-content: center;
                }}
                
                .copy-btn {{ 
                    background: linear-gradient(135deg, #4CAF50, #45a049);
                    color: white; 
                    padding: 15px 25px; 
                    border: none; 
                    border-radius: 10px;
                    cursor: pointer; 
                    font-weight: 600;
                    font-size: 1rem;
                    transition: all 0.3s ease;
                    box-shadow: 0 4px 15px rgba(76, 175, 80, 0.3);
                }}
                
                .copy-btn:hover {{ 
                    transform: translateY(-2px);
                    box-shadow: 0 6px 20px rgba(76, 175, 80, 0.4);
                    background: linear-gradient(135deg, #45a049, #4CAF50);
                }}
                
                .table-container {{ 
                    padding: 30px;
                    background: white;
                    overflow-x: auto;
                }}
                
                table {{ 
                    width: 100%; 
                    border-collapse: separate;
                    border-spacing: 0;
                    background: white;
                    border-radius: 15px;
                    overflow: hidden;
                    box-shadow: 0 10px 30px rgba(0,0,0,0.05);
                }}
                
                th {{ 
                    background: linear-gradient(135deg, #2c3e50, #34495e);
                    color: white;
                    padding: 20px 15px;
                    text-align: left;
                    font-weight: 600;
                    font-size: 0.95rem;
                    text-transform: uppercase;
                    letter-spacing: 0.5px;
                    position: sticky;
                    top: 0;
                    z-index: 10;
                }}
                
                td {{ 
                    padding: 18px 15px;
                    border-bottom: 1px solid #f1f3f4;
                    font-size: 0.95rem;
                    transition: all 0.2s ease;
                }}
                
                tr:hover td {{ 
                    background: #f8f9fa;
                    transform: scale(1.01);
                }}
                
                tr:hover td:first-child {{ 
                    border-radius: 10px 0 0 10px;
                }}
                
                tr:hover td:last-child {{ 
                    border-radius: 0 10px 10px 0;
                }}
                
                tbody tr {{ 
                    transition: all 0.3s ease;
                    cursor: pointer;
                }}
                
                tbody tr:hover {{ 
                    background: linear-gradient(90deg, #f8f9fa, #ffffff);
                    box-shadow: 0 5px 15px rgba(0,0,0,0.08);
                    transform: translateY(-1px);
                }}
                
                .date-cell {{ 
                    font-weight: 600;
                    color: #2c3e50;
                    font-family: 'Courier New', monospace;
                }}
                
                .activity-cell {{ 
                    font-weight: 500;
                    color: #34495e;
                    max-width: 300px;
                    overflow: hidden;
                    text-overflow: ellipsis;
                    white-space: nowrap;
                }}
                
                .distance-cell {{ 
                    font-weight: 700;
                    color: #27ae60;
                    text-align: center;
                }}
                
                .time-cell {{ 
                    font-weight: 600;
                    color: #2980b9;
                    text-align: center;
                    font-family: 'Courier New', monospace;
                }}
                
                .pace-cell {{ 
                    font-weight: 700;
                    color: #e74c3c;
                    text-align: center;
                    font-family: 'Courier New', monospace;
                }}
                
                @media (max-width: 768px) {{
                    .header {{ flex-direction: column; text-align: center; }}
                    .title {{ font-size: 2rem; }}
                    .button-container {{ flex-direction: column; align-items: center; }}
                    .copy-btn {{ width: 100%; max-width: 300px; }}
                    table {{ font-size: 0.85rem; }}
                    th, td {{ padding: 12px 8px; }}
                }}
                
                .loading {{ 
                    display: none;
                    text-align: center;
                    padding: 20px;
                    color: #666;
                }}
                
                .spinner {{ 
                    border: 3px solid #f3f3f3;
                    border-top: 3px solid #FC4C02;
                    border-radius: 50%;
                    width: 30px;
                    height: 30px;
                    animation: spin 1s linear infinite;
                    margin: 0 auto 10px;
                }}
                
                @keyframes spin {{
                    0% {{ transform: rotate(0deg); }}
                    100% {{ transform: rotate(360deg); }}
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div>
                        <h1 class="title">{athlete_name_display}'s Year-End Running Summary - 2025</h1>
                        <div class="stats">
                            <p><strong>Total Activities (All Time):</strong> {total_activities_count}</p>
                            <p><strong>2025 Runs:</strong> {runs_2025_count}</p>
                            <p><strong>Other Activities:</strong> {other_activities_count}</p>
                            {display_info}
                        </div>
                    </div>
                    <div>
                        <a href="/logout" style="color: white; text-decoration: none; font-weight: 600;">Logout</a>
                    </div>
                </div>
                
                <div class="button-container">
                    <button class="copy-btn" onclick="copyTableData()">Copy 2025 Running Data for ChatGPT</button>
                    <button class="copy-btn" onclick="copyWithPrompts()">Copy Data with Analysis Prompts</button>
                    <button class="copy-btn" onclick="copyPosterPrompt()">Copy Poster Creation Prompt</button>
                </div>
                
                <div class="table-container">
                    <div class="loading" id="loading">
                        <div class="spinner"></div>
                        <p>Loading activities...</p>
                    </div>
                    <table id="activityTable">
                        <thead>
                            <tr><th>Date & Time (IST)</th><th>Activity Name</th><th>Distance (km)</th><th>Duration</th><th>Pace (min/km)</th></tr>
                        </thead>
                        <tbody>
                            {table_rows}
                        </tbody>
                    </table>
                </div>
            </div>
            
            <script>
                // Auto-logout after 1 minute of inactivity
                let inactivityTime = function() {
                    let time;
                    
                    // Reset the timer on any of these events
                    window.onload = resetTimer;
                    document.onmousemove = resetTimer;
                    document.onkeypress = resetTimer;
                    
                    function logout() {
                        // Show a message before logging out
                        const logoutMsg = document.createElement('div');
                        logoutMsg.style.position = 'fixed';
                        logoutMsg.style.bottom = '20px';
                        logoutMsg.style.right = '20px';
                        logoutMsg.style.backgroundColor = '#f8d7da';
                        logoutMsg.style.color = '#721c24';
                        logoutMsg.style.padding = '15px';
                        logoutMsg.style.borderRadius = '5px';
                        logoutMsg.style.boxShadow = '0 2px 10px rgba(0,0,0,0.1)';
                        logoutMsg.style.zIndex = '1000';
                        logoutMsg.style.transition = 'opacity 0.5s';
                        
                        // Add countdown
                        let seconds = 5;
                        logoutMsg.innerHTML = `You will be logged out in ${seconds} seconds...`;
                        document.body.appendChild(logoutMsg);
                        
                        const countdown = setInterval(() => {
                            seconds--;
                            if (seconds <= 0) {
                                clearInterval(countdown);
                                logoutMsg.style.opacity = '0';
                                setTimeout(() => {
                                    window.location.href = '/logout';
                                }, 500);
                            } else {
                                logoutMsg.innerHTML = `You will be logged out in ${seconds} seconds...`;
                            }
                        }, 1000);
                    }
                    
                    function resetTimer() {
                        clearTimeout(time);
                        // 1 minute = 60000 milliseconds
                        time = setTimeout(logout, 60000);
                    }
                    
                    // Start the timer
                    resetTimer();
                };
                
                // Initialize the inactivity timer
                inactivityTime();
                // Add interactive features
                document.addEventListener('DOMContentLoaded', function() {{
                    // Hide loading spinner
                    document.getElementById('loading').style.display = 'none';
                    
                    // Add click handlers to table rows
                    const rows = document.querySelectorAll('tbody tr');
                    rows.forEach(row => {{
                        row.addEventListener('click', function() {{
                            // Highlight selected row
                            rows.forEach(r => r.style.background = '');
                            this.style.background = 'linear-gradient(90deg, #e3f2fd, #ffffff)';
                        }});
                    }});
                    
                    // Add button hover effects
                    const buttons = document.querySelectorAll('.copy-btn');
                    buttons.forEach(btn => {{
                        btn.addEventListener('mouseenter', function() {{
                            this.style.transform = 'translateY(-2px) scale(1.05)';
                        }});
                        btn.addEventListener('mouseleave', function() {{
                            this.style.transform = 'translateY(0) scale(1)';
                        }});
                    }});
                }});
                
                function copyTableData() {{
                    const table = document.getElementById('activityTable');
                    const rows = table.getElementsByTagName('tr');
                    let data = 'Date,Activity,Distance (km),Time,Pace (min/km)\\n';
                    
                    for (let i = 1; i < rows.length; i++) {{
                        const cells = rows[i].getElementsByTagName('td');
                        const rowData = [];
                        for (let j = 0; j < cells.length; j++) {{
                            rowData.push(cells[j].innerText);
                        }}
                        data += rowData.join(',') + '\\n';
                    }}
                    
                    navigator.clipboard.writeText(data).then(function() {{
                        alert('2025 running data copied to clipboard! You can now paste this into ChatGPT for poster generation.');
                    }});
                }}
                
                function copyWithPrompts() {{
                    const csvData = `{csv_data}`;
                    
                    const prompts = `
                    
=== CHATGPT PROMPTS FOR STRAVA DATA ANALYSIS ===

PROMPT 1: Basic Analysis
"Analyze this running data and provide insights on:
1. Performance trends and improvements
2. Training consistency patterns  
3. Goal achievement status
4. Recommendations for future training

Data:
${{csvData}}"

PROMPT 2: Visual Poster Creation
"Create a visually appealing text-based poster/infographic from this running data in a Spotify Wrapped style. Include:
- Total distance and time statistics
- Monthly breakdowns with progress indicators
- Fastest/longest run highlights
- Consistency streaks and patterns
- Fun personality insights (early bird vs night owl)
- Motivational summary

Use emojis, creative formatting, and make it shareable!

Data:
${{csvData}}"

PROMPT 3: Detailed Coaching Analysis
"Act as a professional running coach and analyze this data comprehensively:
1. Pace analysis and efficiency trends
2. Weekly/monthly volume patterns
3. Recovery and injury risk assessment
4. Specific workout recommendations
5. Long-term development plan

Provide actionable advice with specific metrics.

Data:
${{csvData}}"

PROMPT 4: Social Media Summary
"Create engaging social media captions for different platforms about this running journey:
- Instagram post with stats and achievements
- Twitter summary with key highlights
- Facebook story about progress
- LinkedIn professional development angle

Make it inspiring and shareable!

Data:
${{csvData}}"
`;
                    
                    navigator.clipboard.writeText(prompts.trim()).then(function() {{
                        alert('Data and analysis prompts copied! You now have 4 ready-to-use prompts for ChatGPT along with your running data.');
                    }});
                }}
                
                function copyPosterPrompt() {{
                    const csvData = `{csv_data}`;
                    
                    const posterPrompt = `Create a visually appealing text-based poster/infographic from this running data in a Spotify Wrapped style. Include:
- Total distance and time statistics
- Monthly breakdowns with progress indicators
- Fastest/longest run highlights
- Consistency streaks and patterns
- Fun personality insights (early bird vs night owl)
- Motivational summary

Use emojis, creative formatting, and make it shareable!

Data:
${{csvData}}`;
                    
                    navigator.clipboard.writeText(posterPrompt.trim()).then(function() {{
                        alert('Poster creation prompt copied! You can now paste this into ChatGPT to create your visual running summary.');
                    }});
                }}
            </script>
        </body>
        </html>
        """.format(
            athlete_name_display=athlete_name_display,
            total_activities_count=total_activities_count,
            runs_2025_count=runs_2025_count,
            other_activities_count=other_activities_count,
            display_info=display_info,
            csv_data=csv_data,
            activities=activities,
            runs_2025=runs_2025,
            table_rows=table_rows
        )
        
        return html_content
        
    except Exception as e:
        logger.error(f"Error in get_stats_page: {str(e)}")
        return f'<h1>Error</h1><p>{str(e)}</p><p><a href="/login">Try again</a></p>'

if __name__ == '__main__':
    logger.info("Starting Flask app...")
    logger.info(f"Available routes: {[rule.rule for rule in app.url_map.iter_rules()]}")
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting on port {port}")
    app.run(host='0.0.0.0', port=port)
