# Backtest module Dockerfile
# Independent from the main bot - runs backtests and generates HTML reports

FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backtest module
COPY run_backtest.py .

# Create directories for data and results
RUN mkdir -p /app/data /app/results

# Default entrypoint
ENTRYPOINT ["python", "run_backtest.py"]
