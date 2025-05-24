FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create app user
RUN useradd -m -s /bin/bash pyvisioniq

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY *.py ./
COPY templates ./templates/
COPY csv_manager.py ./
COPY secure_config.py ./

# Create necessary directories
RUN mkdir -p /app/data /app/.pyvisioniq /app/data/data_archive && \
    chown -R pyvisioniq:pyvisioniq /app

# Switch to non-root user
USER pyvisioniq

# Expose port
EXPOSE 8001

# Entry point script to handle password from file
COPY --chown=pyvisioniq:pyvisioniq docker-entrypoint.sh /
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python", "pyvisioniq.py"]