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
import os
import requests
import base64
import json
import logging
import tempfile
import imgkit
from datetime import datetime, timedelta
from functools import wraps
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
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
XAI_API_URL = "https://api.x.ai/v1/chat/completions"

# Warn on startup if credentials are missing
if not CLIENT_ID or not CLIENT_SECRET:
    logger.warning('STRAVA_CLIENT_ID or STRAVA_CLIENT_SECRET not set. Ensure Railway shared variables are configured.')
else:
    logger.info('STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET found in environment')

if not OPENAI_API_KEY:
    logger.warning('OPENAI_API_KEY not set. AI poster generation will not work.')
else:
    logger.info('OPENAI_API_KEY present: True')

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
    """Generate a poster using OpenAI's DALL-E 3 model with running stats."""
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not configured, using mock response")
        return get_mock_poster(activities, athlete_name)
    
    try:
        # Prepare the prompt with activity data
        prompt = f"""
        Create a beautiful, inspiring poster for a runner's year in review with the following details:
        
        - Athlete: {athlete_name}
        - Total Runs: {len(activities)}
        - Total Distance: {sum(a.get('m', 0) / 1000 for a in activities):.1f} km
        - Total Time: {sum(a.get('e', 0) / 3600 for a in activities):.1f} hours
        
        The poster should be visually stunning and include:
        1. A creative title
        2. Key statistics
        3. A motivational message
        4. A beautiful running-related background
        5. A clean, modern design
        """
        
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        # For DALL-E 3
        payload = {
            "model": "dall-e-3",
            "prompt": prompt,
            "n": 1,
            "size": "1024x1024",
            "quality": "standard"
        }
        
        logger.info("Sending request to OpenAI DALL-E API...")
        response = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers=headers,
            json=payload
        )
        
        # Log response for debugging
        logger.info(f"API Response Status: {response.status_code}")
        
        if response.status_code != 200:
            logger.error(f"API Error: {response.status_code} - {response.text}")
            logger.warning("Falling back to mock response")
            return get_mock_poster(activities, athlete_name)
            
        result = response.json()
        
        # Extract the image URL
        if 'data' in result and len(result['data']) > 0:
            image_url = result['data'][0].get('url')
            if image_url:
                # Create an HTML page that displays the generated image
                return f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>{athlete_name}'s Year in Running</title>
                    <style>
                        body {{
                            margin: 0;
                            padding: 0;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            min-height: 100vh;
                            background-color: #f5f5f5;
                        }}
                        .poster-container {{
                            max-width: 100%;
                            text-align: center;
                        }}
                        .poster-image {{
                            max-width: 100%;
                            height: auto;
                            border-radius: 10px;
                            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
                        }}
                        .download-btn {{
                            margin-top: 20px;
                            padding: 10px 20px;
                            background-color: #4CAF50;
                            color: white;
                            border: none;
                            border-radius: 5px;
                            cursor: pointer;
                            font-size: 16px;
                            text-decoration: none;
                            display: inline-block;
                        }}
                    </style>
                </head>
                <body>
                    <div class="poster-container">
                        <img src="{image_url}" alt="Running Poster" class="poster-image">
                        <div>
                            <a href="{image_url}" download="running-poster.png" class="download-btn">
                                Download Poster
                            </a>
                        </div>
                    </div>
                </body>
                </html>
                """
        
        logger.warning("Unexpected API response format, using mock response")
        return get_mock_poster(activities, athlete_name)
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {str(e)}")
        logger.warning("Falling back to mock response")
        return get_mock_poster(activities, athlete_name)
    except Exception as e:
        logger.error(f"Error generating poster: {str(e)}", exc_info=True)
        logger.warning("Falling back to mock response")
        return get_mock_poster(activities, athlete_name)

@app.route('/logout')
def logout():
    """Log out the user by clearing the session."""
    session.clear()
    return redirect('/')

def html_to_image(html_content, output_path=None):
    """Convert HTML content to an image and return the file path."""
    try:
        # Create a temporary file for the HTML
        with tempfile.NamedTemporaryFile(delete=False, suffix='.html') as f:
            f.write(html_content.encode('utf-8'))
            html_path = f.name

        # If no output path is provided, create a temporary file
        if not output_path:
            output_path = os.path.join(tempfile.gettempdir(), f"running_poster_{int(datetime.now().timestamp())}.png")
        
        # Configure imgkit with options for better quality
        options = {
            'format': 'png',
            'encoding': 'UTF-8',
            'quality': '100',
            'width': '900',
            'disable-javascript': '',
            'quiet': ''
        }
        
        # Convert HTML to image
        imgkit.from_file(html_path, output_path, options=options)
        
        # Clean up temporary HTML file
        os.unlink(html_path)
        
        return output_path
    except Exception as e:
        logger.error(f"Error converting HTML to image: {str(e)}")
        return None

def get_mock_poster(activities, athlete_name, return_as_image=False):
    """Generate a beautiful poster with running statistics using HTML/CSS."""
    # Calculate statistics
    total_runs = len(activities)
    total_distance = sum(a.get('m', 0) / 1000 for a in activities)  # Convert to km
    total_time = sum(a.get('e', 0) / 3600 for a in activities)  # Convert to hours
    avg_distance = total_distance / total_runs if total_runs > 0 else 0
    avg_pace = (total_time * 60) / total_distance if total_distance > 0 else 0
    
    current_year = datetime.now().year
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{athlete_name}'s {current_year} Running Stats</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600;700&display=swap');
            
            :root {{
                --primary: #4a6fa5;
                --secondary: #ff6b6b;
                --accent: #4ecdc4;
                --dark: #2c3e50;
                --light: #f8f9fa;
            }}
            
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            
            body {{
                font-family: 'Poppins', sans-serif;
                background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
                min-height: 100vh;
                padding: 2rem;
                color: var(--dark);
                line-height: 1.6;
            }}
            
            .poster {{
                max-width: 900px;
                margin: 0 auto;
                background: white;
                border-radius: 20px;
                overflow: hidden;
                box-shadow: 0 10px 30px rgba(0,0,0,0.1);
            }}
            
            .header {{
                background: linear-gradient(135deg, var(--primary) 0%, #2c3e50 100%);
                color: white;
                padding: 2.5rem;
                text-align: center;
                position: relative;
                overflow: hidden;
            }}
            
            .header h1 {{
                font-size: 2.5rem;
                margin-bottom: 0.5rem;
                font-weight: 700;
            }}
            
            .header p {{
                font-size: 1.2rem;
                opacity: 0.9;
            }}
            
            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 1.5rem;
                padding: 2.5rem;
            }}
            
            .stat-card {{
                background: white;
                border-radius: 15px;
                padding: 1.5rem;
                text-align: center;
                box-shadow: 0 5px 15px rgba(0,0,0,0.05);
                transition: transform 0.3s ease, box-shadow 0.3s ease;
                border: 1px solid rgba(0,0,0,0.05);
            }}
            
            .stat-card:hover {{
                transform: translateY(-5px);
                box-shadow: 0 8px 25px rgba(0,0,0,0.1);
            }}
            
            .stat-value {{
                font-size: 2.5rem;
                font-weight: 700;
                color: var(--primary);
                margin: 0.5rem 0;
                line-height: 1.2;
            }}
            
            .stat-label {{
                color: #666;
                font-size: 0.95rem;
                text-transform: uppercase;
                letter-spacing: 1px;
                font-weight: 600;
            }}
            
            .footer {{
                text-align: center;
                padding: 2rem;
                background: #f8f9fa;
                border-top: 1px solid #eee;
                font-size: 0.9rem;
                color: #666;
            }}
            
            .quote {{
                font-style: italic;
                text-align: center;
                padding: 1.5rem 2.5rem;
                font-size: 1.1rem;
                color: #555;
                border-top: 1px solid #eee;
                border-bottom: 1px solid #eee;
                margin: 0 2.5rem;
                background: #fcfcfc;
                border-radius: 10px;
                position: relative;
                top: -1.5rem;
                max-width: calc(100% - 5rem);
                margin: 0 auto;
                box-shadow: 0 5px 15px rgba(0,0,0,0.02);
            }}
            
            .quote:before {{
                content: '""';
                font-size: 4rem;
                color: var(--accent);
                opacity: 0.2;
                position: absolute;
                top: -1rem;
                left: 1rem;
                font-family: serif;
                line-height: 1;
            }}
            
            .running-icon {{
                font-size: 2rem;
                margin-bottom: 1rem;
                display: inline-block;
            }}
            
            @media (max-width: 768px) {{
                .stats-grid {{
                    grid-template-columns: 1fr;
                    padding: 1.5rem;
                }}
                
                .header h1 {{
                    font-size: 2rem;
                }}
                
                .quote {{
                    margin: 0 1rem;
                    padding: 1rem;
                    max-width: calc(100% - 2rem);
                }}
            }}
        </style>
    </head>
    <body>
        <div class="poster">
            <div class="header">
                <div class="running-icon">🏃‍♂️</div>
                <h1>{athlete_name}'s {current_year} Running Journey</h1>
                <p>Your year in numbers</p>
            </div>
            
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-value">{total_runs}</div>
                    <div class="stat-label">Total Runs</div>
                </div>
                
                <div class="stat-card">
                    <div class="stat-value">{total_distance:,.1f}<span style="font-size: 1.5rem">km</span></div>
                    <div class="stat-label">Total Distance</div>
                </div>
                
                <div class="stat-card">
                    <div class="stat-value">{total_time:,.1f}<span style="font-size: 1.5rem">hrs</span></div>
                    <div class="stat-label">Total Time</div>
                </div>
                
                <div class="stat-card">
                    <div class="stat-value">{avg_distance:.1f}<span style="font-size: 1.5rem">km</span></div>
                    <div class="stat-label">Avg. Distance/Run</div>
                </div>
            </div>
            
            <div class="quote">
                "The miracle isn't that I finished. The miracle is that I had the courage to start."
                <div style="margin-top: 0.5rem; font-size: 0.9rem; color: #4ecdc4;">— John Bingham</div>
            </div>
            
            <div class="footer">
                Generated with ❤️ using Strava Year in Review • {datetime.now().strftime('%B %d, %Y')}
            </div>
        </div>
    </body>
    </html>
    """
    
    if not return_as_image:
        return html_content
    
    try:
        # Generate the image
        image_path = html_to_image(html_content)
        if not image_path or not os.path.exists(image_path):
            return None, None
            
        # Read the image file and convert to base64
        with open(image_path, 'rb') as img_file:
            image_data = base64.b64encode(img_file.read()).decode('utf-8')
            image_data_url = f"data:image/png;base64,{image_data}"
            
        return image_data_url, image_path
    except Exception as e:
        logger.error(f"Error generating image: {str(e)}")
        return None, None

@app.route('/download-poster')
def download_poster():
    try:
        if 'activities' not in session:
            return "No activities found. Please generate a poster first.", 400
            
        activities = session['activities']
        athlete_name = session.get('athlete_name', 'Runner')
        
        # Generate the image
        image_data_url, image_path = get_mock_poster(activities, athlete_name, return_as_image=True)
        
        if not image_path or not os.path.exists(image_path):
            return "Failed to generate poster image.", 500
            
        # Clean up the temporary file after sending
        def cleanup():
            if os.path.exists(image_path):
                try:
                    os.unlink(image_path)
                except Exception as e:
                    logger.error(f"Error cleaning up image file: {str(e)}")
        
        # Return the image for download
        response = send_file(
            image_path,
            mimetype='image/png',
            as_attachment=True,
            download_name=f"{athlete_name}_running_stats_{datetime.now().strftime('%Y%m%d')}.png"
        )
        
        # Set up cleanup after the response is sent
        response.call_on_close(cleanup)
        return response
        
    except Exception as e:
        logger.error(f"Error in download_poster: {str(e)}")
        return "An error occurred while generating the poster.", 500

# Run the application
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
