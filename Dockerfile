# Dockerfile

FROM python:3.9-slim

# Set environment variables
ENV PYTHONUNBUFFERED 1

# Create working directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt /app/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the Flask application code
COPY . /app/

# Expose the port
EXPOSE 80 443

# Command to run the Flask app
CMD ["gunicorn", "--timeout", "120", "-b", "0.0.0.0:80", "app:app"]

