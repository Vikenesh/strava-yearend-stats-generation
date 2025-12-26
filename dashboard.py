import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime
import os
import json
from collections import defaultdict

# Get port from environment variable or use default
port = int(os.environ.get("PORT", 8501))

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
    """Process activities into a DataFrame with enhanced metrics."""
    if not activities:
        return pd.DataFrame()
        
    df = pd.DataFrame(activities)
    
    # Basic date/time features
    df['date'] = pd.to_datetime(df['start_date']).dt.date
    df['datetime'] = pd.to_datetime(df['start_date'])
    df['month'] = df['datetime'].dt.month_name()
    df['year'] = df['datetime'].dt.year
    df['day_of_week'] = df['datetime'].dt.day_name()
    df['hour'] = df['datetime'].dt.hour
    df['week'] = df['datetime'].dt.isocalendar().week
    df['day_of_year'] = df['datetime'].dt.dayofyear
    
    # Distance and time metrics
    df['distance_km'] = df['distance'] / 1000
    df['moving_time_hrs'] = df['moving_time'] / 3600
    df['elapsed_time_hrs'] = df['elapsed_time'] / 3600
    
    # Calculate pace in min/km (only for runs with valid distance and time)
    df['pace_min_km'] = (df['moving_time'] / 60) / (df['distance_km'] + 1e-6)
    
    # Calculate efficiency metrics
    df['efficiency_ratio'] = df['moving_time'] / (df['elapsed_time'] + 1e-6)
    
    # Extract elevation data if available
    if 'total_elevation_gain' in df.columns:
        df['elevation_gain_m'] = df['total_elevation_gain']
        df['elevation_per_km'] = df['elevation_gain_m'] / (df['distance_km'] + 1e-6)
    
    # Extract heart rate data if available
    if 'average_heartrate' in df.columns:
        df['avg_hr'] = df['average_heartrate']
    if 'max_heartrate' in df.columns:
        df['max_hr'] = df['max_heartrate']
    
    # Calculate effort score (simple version)
    if 'suffer_score' in df.columns:
        df['effort_score'] = df['suffer_score']
    
    return df

def display_activity_metrics(df):
    """Display key activity metrics in a clean layout."""
    if df.empty:
        return
        
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Activities", len(df))
    with col2:
        st.metric("Total Distance (km)", f"{df['distance_km'].sum():.1f}")
    with col3:
        st.metric("Total Moving Time", f"{df['moving_time_hrs'].sum():.1f} hrs")
    with col4:
        if 'elevation_gain_m' in df.columns:
            st.metric("Total Elevation Gain", f"{df['elevation_gain_m'].sum():.0f} m")


def plot_weekly_trends(df):
    """Plot weekly distance and activity count trends."""
    if df.empty or len(df) < 2:
        return
        
    weekly = df.groupby(['year', 'week']).agg({
        'distance_km': 'sum',
        'id': 'count',
        'moving_time_hrs': 'sum'
    }).reset_index()
    
    weekly['week_start'] = weekly.apply(
        lambda x: datetime.strptime(f"{int(x['year'])}-W{int(x['week'])}-1" + " +0000", "%Y-W%W-%w %z"), 
        axis=1
    )
    
    fig = px.line(
        weekly, 
        x='week_start', 
        y=['distance_km', 'id'],
        title='Weekly Activity Trends',
        labels={'value': 'Value', 'variable': 'Metric', 'week_start': 'Week'},
        color_discrete_map={'distance_km': '#636EFA', 'id': '#EF553B'}
    )
    
    fig.update_layout(
        yaxis_title='Value',
        xaxis_title='Week',
        legend_title='Metric',
        hovermode='x unified'
    )
    
    st.plotly_chart(fig, use_container_width=True)


def plot_pace_analysis(df):
    """Plot pace distribution and trends."""
    if df.empty or 'pace_min_km' not in df.columns:
        return
        
    # Filter out extreme values
    df = df[(df['pace_min_km'] > 2) & (df['pace_min_km'] < 15)]
    
    col1, col2 = st.columns(2)
    
    with col1:
        fig = px.histogram(
            df, 
            x='pace_min_km',
            title='Pace Distribution (min/km)',
            labels={'pace_min_km': 'Pace (min/km)'},
            color_discrete_sequence=['#00CC96']
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        if len(df) > 5:  # Need multiple data points for trend
            df_sorted = df.sort_values('date')
            fig = px.scatter(
                df_sorted,
                x='date',
                y='pace_min_km',
                trendline='lowess',
                title='Pace Over Time',
                labels={'pace_min_km': 'Pace (min/km)', 'date': 'Date'}
            )
            st.plotly_chart(fig, use_container_width=True)


def plot_activity_types(df):
    """Plot distribution of activity types."""
    if df.empty or 'type' not in df.columns:
        return
        
    activity_counts = df['type'].value_counts().reset_index()
    activity_counts.columns = ['Activity Type', 'Count']
    
    fig = px.pie(
        activity_counts,
        values='Count',
        names='Activity Type',
        title='Activity Type Distribution',
        color_discrete_sequence=px.colors.qualitative.Pastel
    )
    
    st.plotly_chart(fig, use_container_width=True)


def plot_time_of_day_analysis(df):
    """Analyze and plot activity distribution by time of day."""
    if df.empty or 'hour' not in df.columns:
        return
        
    hour_counts = df['hour'].value_counts().sort_index().reset_index()
    hour_counts.columns = ['Hour', 'Count']
    
    fig = px.bar(
        hour_counts,
        x='Hour',
        y='Count',
        title='Activity Distribution by Hour of Day',
        color_discrete_sequence=['#AB63FA']
    )
    
    fig.update_layout(
        xaxis=dict(tickmode='linear', dtick=1),
        xaxis_title='Hour of Day',
        yaxis_title='Number of Activities'
    )
    
    st.plotly_chart(fig, use_container_width=True)


def plot_elevation_analysis(df):
    """Plot elevation-related metrics if available."""
    if df.empty or 'elevation_gain_m' not in df.columns:
        return
        
    col1, col2 = st.columns(2)
    
    with col1:
        fig = px.scatter(
            df,
            x='distance_km',
            y='elevation_gain_m',
            title='Elevation Gain vs Distance',
            labels={'distance_km': 'Distance (km)', 'elevation_gain_m': 'Elevation Gain (m)'}
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        if 'elevation_per_km' in df.columns:
            fig = px.histogram(
                df,
                x='elevation_per_km',
                title='Elevation per km Distribution',
                labels={'elevation_per_km': 'Elevation Gain per km (m/km)'}
            )
            st.plotly_chart(fig, use_container_width=True)


def plot_heart_rate_analysis(df):
    """Plot heart rate analysis if data is available."""
    if df.empty or 'avg_hr' not in df.columns:
        return
        
    st.subheader("Heart Rate Analysis")
    
    col1, col2 = st.columns(2)
    
    with col1:
        if 'avg_hr' in df.columns:
            fig = px.box(
                df,
                y='avg_hr',
                title='Average Heart Rate Distribution',
                labels={'avg_hr': 'Average HR (bpm)'}
            )
            st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        if 'max_hr' in df.columns:
            fig = px.scatter(
                df,
                x='distance_km',
                y='max_hr',
                title='Max HR vs Distance',
                labels={'distance_km': 'Distance (km)', 'max_hr': 'Max HR (bpm)'}
            )
            st.plotly_chart(fig, use_container_width=True)


def plot_efficiency_analysis(df):
    """Plot efficiency metrics."""
    if df.empty or 'efficiency_ratio' not in df.columns:
        return
        
    fig = px.scatter(
        df,
        x='date',
        y='efficiency_ratio',
        title='Activity Efficiency Over Time',
        labels={'efficiency_ratio': 'Efficiency (Moving Time / Elapsed Time)', 'date': 'Date'},
        trendline='lowess'
    )
    
    fig.update_yaxes(range=[0, 1.1])
    st.plotly_chart(fig, use_container_width=True)


def main():
    st.set_page_config(
        page_title="🏃‍♂️ Your Year in Running",
        page_icon="🏃‍♂️",
        layout="wide"
    )
    
    st.title("🏃‍♂️ Your Year in Running")
    st.markdown("""
        <style>
            .main { background-color: #f8f9fa; }
            .stApp { max-width: 1400px; margin: 0 auto; }
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
        </style>
    """, unsafe_allow_html=True)

    activities = load_activities()
    if not activities:
        st.warning("No activities found. Please log in with Strava first.")
        return

    df = process_activities(activities)
    current_year = datetime.now().year
    df_current_year = df[df['year'] == current_year].copy()
    
    # Filter for runs if you want to focus on running
    runs_df = df[df['type'] == 'Run'].copy()
    
    # Display key metrics
    st.subheader("Activity Overview")
    display_activity_metrics(df)
    
    # Weekly trends
    st.subheader("Weekly Activity Trends")
    plot_weekly_trends(df)
    
    # Activity type distribution
    col1, col2 = st.columns(2)
    with col1:
        plot_activity_types(df)
    with col2:
        plot_time_of_day_analysis(df)
    
    # Pace analysis for runs
    if not runs_df.empty:
        st.subheader("Running Analysis")
        plot_pace_analysis(runs_df)
    
    # Elevation analysis
    if 'elevation_gain_m' in df.columns:
        st.subheader("Elevation Analysis")
        plot_elevation_analysis(df[df['distance_km'] > 0])  # Only activities with distance
    
    # Heart rate analysis if data is available
    plot_heart_rate_analysis(df)
    
    # Efficiency analysis
    plot_efficiency_analysis(df)

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
    import streamlit.web.cli as stcli
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--server.port":
        port = int(sys.argv[2])
    
    # Set the Streamlit configuration
    st.set_page_config(
        page_title="🏃‍♂️ Year in Running",
        page_icon="🏃‍♂️",
        layout="wide"
    )
    
    # Run the main function
    main()
    
    # This is needed for Railway deployment
    st._main_run_clExplicit()
