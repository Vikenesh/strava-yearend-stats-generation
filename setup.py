from setuptools import setup, find_packages

setup(
    name="strava-year-end-summary",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        'Flask==2.3.3',
        'requests==2.31.0',
        'gunicorn==21.2.0',
        'pandas==1.5.3',  # Downgraded to a version with better wheel support
        'numpy==1.24.3',  # Compatible version with pandas 1.5.3
        'plotly==5.15.0',
        'python-dotenv==1.0.0',
        'python-dateutil==2.8.2',
        'pytz==2023.3',
        'openai==1.3.5',
    ],
    python_requires='>=3.8',
)
