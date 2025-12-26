import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime
import os
import json
from collections import defaultdict

# Set page config
st.set_page_config(
    page_title="🏃‍♂️ Year in Running",
    page_icon="🏃‍♂️",
    layout="wide"
)

# Custom CSS
st.markdown("""
<style>
    .main { background-color: #f8f9fa; }
    .stApp { max-width: 1200px; margin: 0 auto; }
    .metric-card {
        background: white;
        padding: 20px;
        border-radius: 10px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        margin-bottom: 20px;
    }
    .highlight {
        background: linear-gradient(135deg, #6a11cb 0%, #2575fc 100%);
        color: white;
        padding: 20px;
        border-radius: 10px;
        margin: 20px 0;
    }
    @media (max-width: 768px) {
        .highlight { padding: 15px; }
    }
</style>
""", unsafe_allow_html=True)

def load_activities():
    """Load activities from the session file."""
    try:
        with open('/tmp/activities.json', 'r') as f:
            return json.load(f)
    except:
        return []

def process_activities(activities):
    """Process activities into a DataFrame."""
    if not activities:
        return pd.DataFrame()
        
    df = pd.DataFrame(activities)
    df['date'] = pd.to_datetime(df['start_date']).dt.date
    df['month'] = pd.to_datetime(df['date']).dt.month_name()
    df['year'] = pd.to_datetime(df['date']).dt.year
    df['day_of_week'] = pd.to_datetime(df['date']).dt.day_name()
    df['hour'] = pd.to_datetime(df['start_date']).dt.hour
    df['distance_km'] = df['distance'] / 1000
    df['moving_time_hrs'] = df['moving_time'] / 3600
    return df

def main():
    st.title("🏃‍♂️ Your Year in Running")
    st.markdown("Visualize your running journey with beautiful statistics and insights.")

    activities = load_activities()
    if not activities:
        st.warning("No activities found. Please log in with Strava first.")
        return

    df = process_activities(activities)
    current_year = datetime.now().year
    df_current_year = df[df['year'] == current_year].copy()

    # 1. Activity Type Distribution
    st.header("1. Activity Breakdown")
    if not df.empty:
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("### By Activity Type")
            activity_counts = df['type'].value_counts().reset_index()
            fig = px.pie(activity_counts, values='count', names='type', 
                         color_discrete_sequence=px.colors.qualitative.Pastel)
            st.plotly_chart(fig, use_container_width=True)
        
        with col2:
            st.markdown(f"### Activities in {current_year}")
            monthly_activities = df_current_year['month'].value_counts().reset_index()
            monthly_activities.columns = ['Month', 'Count']
            fig = px.bar(monthly_activities, x='Month', y='Count',
                         title=f"Activities by Month - {current_year}",
                         color_discrete_sequence=['#6a11cb'])
            st.plotly_chart(fig, use_container_width=True)

    # 2. Monthly Stats
    st.header("2. Monthly Statistics")
    if not df.empty:
        monthly_stats = df.groupby(['year', 'month']).agg({
            'distance_km': 'sum',
            'moving_time_hrs': 'sum'
        }).reset_index()
        
        fig = px.bar(monthly_stats, x='month', y='distance_km',
                     title="Monthly Distance (km)",
                     color_discrete_sequence=['#2575fc'])
        st.plotly_chart(fig, use_container_width=True)

    # 3. Maximum Mileage Month
    st.header("3. Peak Performance")
    if not monthly_stats.empty:
        max_month = monthly_stats.loc[monthly_stats['distance_km'].idxmax()]
        st.markdown(f"""
        <div class="highlight">
            <h3>🏆 Best Month</h3>
            <p>Your highest mileage month was <strong>{max_month['month']} {int(max_month['year'])}</strong> with:</p>
            <p>• <strong>{max_month['distance_km']:.1f} km</strong> total distance</p>
            <p>• <strong>{max_month['moving_time_hrs']:.1f} hours</strong> of activity</p>
        </div>
        """, unsafe_allow_html=True)

    # 4. Consistency Streak
    st.header("4. Consistency Tracker")
    if not df.empty:
        dates = sorted(df['date'].unique())
        current_streak = 1
        max_streak = 1
        
        for i in range(1, len(dates)):
            if (dates[i] - dates[i-1]).days == 1:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 1
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("🔥 Longest Streak", f"{max_streak} days")
        with col2:
            st.metric("📅 Total Active Days", len(dates))

    # 5. Most Active Workout Time
    st.header("5. Your Prime Time")
    if not df.empty and 'hour' in df.columns:
        hourly_activities = df['hour'].value_counts().sort_index().reset_index()
        hourly_activities.columns = ['Hour', 'Count']
        
        fig = px.line_polar(hourly_activities, r='Count', theta='Hour',
                           line_close=True, color_discrete_sequence=['#ff6b6b'],
                           title="Activity Distribution by Hour of Day")
        st.plotly_chart(fig, use_container_width=True)

    # 6. Total Hours Spent
    st.header("6. Time Invested")
    if not df.empty:
        total_hours = df['moving_time_hrs'].sum()
        st.metric("⏱️ Total Hours", f"{total_hours:.1f} hours")

    # 7. Fastest Run
    st.header("7. Speed Demon")
    if 'Run' in df['type'].values:
        runs = df[df['type'] == 'Run'].copy()
        runs['pace'] = runs['moving_time'] / 60 / runs['distance_km']
        fastest_run = runs.loc[runs['pace'].idxmin()]
        
        st.markdown(f"""
        <div class="metric-card">
            <h3>⚡ Your Fastest Run</h3>
            <p><strong>Date:</strong> {fastest_run['date'].strftime('%B %d, %Y')}</p>
            <p><strong>Pace:</strong> {fastest_run['pace']:.2f} min/km</p>
            <p><strong>Distance:</strong> {fastest_run['distance_km']:.2f} km</p>
            <p><strong>Time:</strong> {int(fastest_run['moving_time']//60)} minutes</p>
        </div>
        """, unsafe_allow_html=True)

    # 8. Longest Run
    st.header("8. Endurance Champion")
    if 'Run' in df['type'].values:
        longest_run = runs.loc[runs['distance_km'].idxmax()]
        
        st.markdown(f"""
        <div class="metric-card">
            <h3>🏆 Your Longest Run</h3>
            <p><strong>Date:</strong> {longest_run['date'].strftime('%B %d, %Y')}</p>
            <p><strong>Distance:</strong> {longest_run['distance_km']:.2f} km</p>
            <p><strong>Time:</strong> {int(longest_run['moving_time']//60)} minutes</p>
            <p><strong>Pace:</strong> {longest_run['pace']:.2f} min/km</p>
        </div>
        """, unsafe_allow_html=True)

    # Footer
    st.markdown("---")
    st.markdown("### 📱 Share Your Stats")
    st.markdown("Take a screenshot to share your stats on social media!")

if __name__ == "__main__":
    main()
