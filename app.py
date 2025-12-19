from flask import Flask, redirect, request, session
import requests
import time
import datetime
import json
import os
from datetime import datetime as dt, timezone
from collections import defaultdict, Counter
import calendar

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
    utc_dt = dt.fromisoformat(utc_datetime_str.replace('Z', '+00:00'))
    ist_dt = utc_dt.astimezone(timezone(datetime.timedelta(hours=5, minutes=30)))
    return ist_dt

def analyze_wrapped_stats(activities):
    """Analyze activities for wrapped-style visualization"""
    runs = [a for a in activities if a['type'] == 'Run']
    
    if not runs:
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
    today = dt.now(timezone(datetime.timedelta(hours=5, minutes=30))).date()
    if dates and (today - dates[-1]).days <= 1:
        current_streak = temp_streak
    
    # Favorite day of week
    day_counts = Counter(run['ist_date'].strftime('%A') for run in ist_runs)
    favorite_day = day_counts.most_common(1)[0] if day_counts else ('None', 0)
    
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

def get_all_activities(token):
    headers = {'Authorization': f'Bearer {token}'}
    all_activities = []
    page = 1
    
    print("Fetching activities...")
    while True:
        url = f'https://www.strava.com/api/v3/athlete/activities?page={page}&per_page=200'
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            print(f"Error fetching page {page}: {response.status_code}")
            break
            
        data = response.json()
        
        if not data:  # No more activities
            print(f"Fetched {len(all_activities)} total activities from {page-1} pages")
            break
            
        all_activities.extend(data)
        print(f"Fetched page {page}, got {len(data)} activities, total so far: {len(all_activities)}")
        page += 1
        
        # Safety check to prevent infinite loops
        if page > 100:  # Strava typically limits to around 30-50 pages max
            print("Reached safety limit of 100 pages")
            break
    
    return all_activities

def analyze_with_chatgpt(activities, athlete_name):
    """Send Strava data to ChatGPT for analysis"""
    if OPENAI_API_KEY == 'your-openai-api-key-here':
        return "OpenAI API key not configured. Please set OPENAI_API_KEY environment variable."
    
    # Prepare data for ChatGPT
    runs_2025 = [a for a in activities if a['type'] == 'Run' and a['start_date'].startswith('2025')]
    
    # Create summary data
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
        
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            return f"OpenAI API Error: {response.status_code} - {response.text}"
            
    except Exception as e:
        return f"Error calling OpenAI API: {str(e)}"

@app.route('/')
def index():
    print(f"DEBUG: Index route accessed")
    print(f"DEBUG: Session keys: {list(session.keys())}")
    
    if 'access_token' in session:
        print(f"DEBUG: User is logged in, showing stats page")
        return get_stats_page()
    else:
        print(f"DEBUG: User not logged in, showing login page")
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Strava Year-End Running Summary 2025</title>
            <style>
                * {
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                }
                
                body {
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    min-height: 100vh;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    overflow: hidden;
                }
                
                .container {
                    text-align: center;
                    padding: 2rem;
                    max-width: 600px;
                    position: relative;
                }
                
                .year-display {
                    font-size: 4rem;
                    font-weight: bold;
                    color: #ffffff;
                    text-shadow: 3px 3px 6px rgba(0,0,0,0,0.3);
                    margin-bottom: 1rem;
                    animation: glow 2s ease-in-out infinite alternate;
                }
                
                @keyframes glow {
                    from { text-shadow: 3px 3px 6px rgba(0,0,0,0,0.3); }
                    to { text-shadow: 3px 3px 20px rgba(255,255,255,255,0.5); }
                }
                
                .title {
                    font-size: 2.5rem;
                    color: #ffffff;
                    margin-bottom: 2rem;
                    font-weight: 300;
                }
                
                .subtitle {
                    font-size: 1.2rem;
                    color: #e0e0e0;
                    margin-bottom: 3rem;
                    line-height: 1.6;
                }
                
                .login-btn {
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
                }
                
                .login-btn:hover {
                    transform: translateY(-3px);
                    box-shadow: 0 8px 25px rgba(238, 82, 83, 0.6);
                    background: linear-gradient(45deg, #ee5a52, #ff6b6b);
                }
                
                .calendar-icon {
                    font-size: 3rem;
                    margin-bottom: 1rem;
                    animation: spin 20s linear infinite;
                }
                
                @keyframes spin {
                    from { transform: rotate(0deg); }
                    to { transform: rotate(360deg); }
                }
                
                .confetti {
                    position: absolute;
                    width: 10px;
                    height: 10px;
                    background: #ff6b6b;
                    animation: fall 3s linear infinite;
                }
                
                .confetti:nth-child(1) { left: 10%; animation-delay: 0s; background: #ff6b6b; }
                .confetti:nth-child(2) { left: 20%; animation-delay: 0.5s; background: #4ecdc4; }
                .confetti:nth-child(3) { left: 30%; animation-delay: 1s; background: #45b7d1; }
                .confetti:nth-child(4) { left: 40%; animation-delay: 1.5s; background: #f9ca24; }
                .confetti:nth-child(5) { left: 50%; animation-delay: 2s; background: #f4d03f; }
                .confetti:nth-child(6) { left: 60%; animation-delay: 2.5s; background: #6c5ce7; }
                .confetti:nth-child(7) { left: 70%; animation-delay: 0.3s; background: #a8e6cf; }
                .confetti:nth-child(8) { left: 80%; animation-delay: 0.8s; background: #ffd700; }
                .confetti:nth-child(9) { left: 90%; animation-delay: 1.3s; background: #ff69b4; }
                
                @keyframes fall {
                    0% { transform: translateY(-100vh) rotate(0deg); opacity: 1; }
                    100% { transform: translateY(100vh) rotate(360deg); opacity: 0; }
                }
                
                .features {
                    margin-top: 2rem;
                    color: #e0e0e0;
                }
                
                .feature {
                    margin: 0.5rem 0;
                    font-size: 0.9rem;
                }
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
        '''

@app.route('/login')
def login():
    print(f"DEBUG: Login route accessed")
    print(f"DEBUG: CLIENT_ID = {CLIENT_ID}")
    print(f"DEBUG: REDIRECT_URI = {REDIRECT_URI}")
    auth_url = f'https://www.strava.com/oauth/authorize?client_id={CLIENT_ID}&response_type=code&redirect_uri={REDIRECT_URI}&scope=read&approval_prompt=force'
    print(f"DEBUG: Full auth URL = {auth_url}")
    print(f"DEBUG: Redirecting to Strava OAuth...")
    return redirect(auth_url)

@app.route('/test')
def test():
    return "Test route is working!"

@app.route('/callback')
def callback():
    print(f"DEBUG: Callback route accessed")
    print(f"DEBUG: Request args: {dict(request.args)}")
    
    code = request.args.get('code')
    error = request.args.get('error')
    
    if error:
        print(f"DEBUG: OAuth error: {error}")
        return f'<h1>OAuth Error</h1><p>Error: {error}</p><p><a href="/">Back to home</a></p>'
    
    if not code:
        print(f"DEBUG: No authorization code received")
        return '<h1>Error</h1><p>No authorization code received</p><p><a href="/">Back to home</a></p>'
    
    print(f"DEBUG: Received authorization code: {code[:10]}...")
    
    token_data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'code': code,
        'grant_type': 'authorization_code'
    }
    
    print(f"DEBUG: Requesting access token...")
    response = requests.post('https://www.strava.com/oauth/token', data=token_data)
    
    print(f"DEBUG: Token response status: {response.status_code}")
    print(f"DEBUG: Token response: {response.text[:200]}...")
    
    if response.status_code != 200:
        print(f"DEBUG: Token exchange failed")
        return f'<h1>Error</h1><p>Failed to exchange code for token: {response.text}</p><p><a href="/">Back to home</a></p>'
    
    token_response = response.json()
    session['access_token'] = token_response['access_token']
    session['athlete_info'] = token_response.get('athlete', {})
    
    print(f"DEBUG: Successfully obtained access token")
    print(f"DEBUG: Athlete info: {session['athlete_info'].get('firstname', 'Unknown')} {session['athlete_info'].get('lastname', '')}")
    
    return redirect('/')

@app.route('/callback/')
def callback_with_slash():
    print(f"DEBUG: Callback with slash route accessed")
    return "Callback with slash works!"

@app.route('/logout')
def logout():
    session.clear()
    return '<h1>Logged Out</h1><p><a href="/login">Login again</a></p>'

@app.route('/analyze')
def analyze():
    """Route to analyze data with ChatGPT"""
    try:
        if 'access_token' not in session:
            return redirect('/login')
        
        token = session['access_token']
        athlete = session.get('athlete_info', {})
        athlete_name = athlete.get('firstname', 'Athlete') + ' ' + athlete.get('lastname', '')
        
        activities = get_all_activities(token)
        analysis = analyze_with_chatgpt(activities, athlete_name)
        
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
        return f'<h1>Error</h1><p>{str(e)}</p><p><a href="/">Back to stats</a></p>'

def get_stats_page():
    try:
        # Get token from session
        if 'access_token' not in session:
            return redirect('/login')
        
        token = session['access_token']
        athlete = session.get('athlete_info', {})
        athlete_name = athlete.get('firstname', 'Athlete') + ' ' + athlete.get('lastname', '')
        
        activities = get_all_activities(token)
        
        # Filter for runs only and 2025 only
        runs_2025 = [a for a in activities if a['type'] == 'Run' and a['start_date'].startswith('2025')]
        
        # Sort by date (newest first)
        runs_2025.sort(key=lambda x: x['start_date'], reverse=True)
        
        # Create table rows for 2025 runs only
        table_rows = ""
        for run in runs_2025:
            date = run['start_date'][:10]  # Just the date part
            name = run['name']
            distance = run['distance'] / 1000  # Convert to km
            time_sec = run['moving_time']
            hours = time_sec // 3600
            minutes = (time_sec % 3600) // 60
            seconds = time_sec % 60
            time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            pace = time_sec / 60 / distance if distance > 0 else 0
            pace_min = int(pace)
            pace_sec = int((pace - pace_min) * 60)
            pace_str = f"{pace_min}:{pace_sec:02d}"
            
            table_rows += f"<tr><td>{date}</td><td>{name}</td><td>{distance:.2f}</td><td>{time_str}</td><td>{pace_str}</td></tr>"
        
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Strava Year-End Running Summary - 2025</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #f2f2f2; }}
                .copy-btn {{ background: #4CAF50; color: white; padding: 10px; cursor: pointer; margin: 10px 0; border: none; border-radius: 4px; }}
                .copy-btn:hover {{ background: #45a049; }}
                .stats {{ background: #f9f9f9; padding: 15px; margin: 10px 0; border-radius: 5px; }}
                .header {{ display: flex; justify-content: space-between; align-items: center; }}
                .title {{ color: #FC4C02; }}
            </style>
        </head>
        <body>
            <div class="header">
                <div>
                    <h1 class="title">{athlete_name.strip()}'s Year-End Running Summary - 2025</h1>
                    <div class="stats">
                        <p><strong>Total Activities (All Time):</strong> {len(activities)}</p>
                        <p><strong>2025 Runs:</strong> {len(runs_2025)}</p>
                        <p><strong>Other Activities:</strong> {len(activities) - len([a for a in activities if a['type'] == 'Run'])}</p>
                    </div>
                </div>
                <div>
                    <a href="/logout" style="color: red; text-decoration: none;">Logout</a>
                </div>
            </div>
            
            <button class="copy-btn" onclick="copyTableData()">Copy 2025 Running Data for ChatGPT</button>
            <button class="copy-btn" onclick="copyWithPrompts()">Copy Data with Analysis Prompts</button>
            
            <table id="activityTable">
                <thead>
                    <tr><th>Date</th><th>Activity Name</th><th>Distance (km)</th><th>Time</th><th>Pace (min/km)</th></tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
            
            <script>
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
                    
                    const prompts = `
                    
=== CHATGPT PROMPTS FOR STRAVA DATA ANALYSIS ===

PROMPT 1: Basic Analysis
"Analyze this running data and provide insights on:
1. Performance trends and improvements
2. Training consistency patterns  
3. Goal achievement status
4. Recommendations for future training

Data:
${data}"

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
${data}"

PROMPT 3: Detailed Coaching Analysis
"Act as a professional running coach and analyze this data comprehensively:
1. Pace analysis and efficiency trends
2. Weekly/monthly volume patterns
3. Recovery and injury risk assessment
4. Specific workout recommendations
5. Long-term development plan

Provide actionable advice with specific metrics.

Data:
${data}"

PROMPT 4: Social Media Summary
"Create engaging social media captions for different platforms about this running journey:
- Instagram post with stats and achievements
- Twitter summary with key highlights
- Facebook story about progress
- LinkedIn professional development angle

Make it inspiring and shareable!

Data:
${data}"
`;
                    
                    navigator.clipboard.writeText(prompts.trim()).then(function() {{
                        alert('Data and analysis prompts copied! You now have 4 ready-to-use prompts for ChatGPT along with your running data.');
                    }});
                }}
            </script>
        </body>
        </html>
        """
    except Exception as e:
        return f'<h1>Error</h1><p>{str(e)}</p><p><a href="/login">Try again</a></p>'

if __name__ == '__main__':
    print("Starting Flask app...")
    print(f"Available routes: {[rule.rule for rule in app.url_map.iter_rules()]}")
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
