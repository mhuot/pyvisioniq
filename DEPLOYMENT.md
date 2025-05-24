# PyVisionIQ Deployment Guide

This guide covers various methods to deploy PyVisionIQ as a service with encrypted credentials.

## Prerequisites

1. Initialize encrypted configuration:
   ```bash
   python secure_config.py init
   # Enter your credentials when prompted
   # Remember your master password!
   ```

2. Test the configuration:
   ```bash
   PYVISIONIQ_MASTER_PASSWORD=your_password python pyvisioniq.py
   ```

## Deployment Options

### Option 1: Systemd Service (Linux)

Best for: Production Linux servers, VPS, Raspberry Pi

```bash
# Run the setup script as root
sudo ./setup-service.sh

# Edit the environment file with your master password
sudo nano /etc/pyvisioniq/pyvisioniq.env

# Start the service
sudo systemctl enable pyvisioniq-secure
sudo systemctl start pyvisioniq-secure

# Check status
sudo systemctl status pyvisioniq-secure
sudo journalctl -u pyvisioniq-secure -f
```

### Option 2: Docker

Best for: Container-based deployments, microservices

```bash
# Create password file (more secure than environment variable)
echo "your_master_password" > pyvisioniq_password.txt
chmod 600 pyvisioniq_password.txt

# Build and run
docker-compose up -d

# Check logs
docker-compose logs -f
```

### Option 3: Supervisor

Best for: Shared hosting, when systemd is not available

```bash
# Install supervisor
sudo apt-get install supervisor

# Copy configuration
sudo cp supervisor/pyvisioniq.conf /etc/supervisor/conf.d/

# Set environment variable
export PYVISIONIQ_MASTER_PASSWORD=your_password
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start pyvisioniq
```

### Option 4: PM2 (Node.js Process Manager)

Best for: Development, quick deployment

```bash
# Install PM2
npm install -g pm2

# Start with PM2
PYVISIONIQ_MASTER_PASSWORD=your_password pm2 start pyvisioniq.py --interpreter python3

# Save PM2 configuration
pm2 save
pm2 startup
```

### Option 5: macOS LaunchAgent

Best for: macOS systems

Create `~/Library/LaunchAgents/com.pyvisioniq.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.pyvisioniq</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>/Users/yourusername/pyvisioniq/pyvisioniq.py</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYVISIONIQ_MASTER_PASSWORD</key>
        <string>your_password</string>
    </dict>
    <key>WorkingDirectory</key>
    <string>/Users/yourusername/pyvisioniq</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/pyvisioniq.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/pyvisioniq.error.log</string>
</dict>
</plist>
```

Load the service:
```bash
launchctl load ~/Library/LaunchAgents/com.pyvisioniq.plist
```

## Security Best Practices

### 1. Secure Password Storage

Never store the master password in:
- Git repositories
- Plain text files (except temporarily)
- Application logs

Instead use:
- Environment variables (from secure sources)
- Secret management services (Vault, AWS Secrets Manager)
- Encrypted files with restricted permissions

### 2. File Permissions

```bash
# Encrypted config should only be readable by service user
chmod 600 .pyvisioniq/config.enc

# Environment files with passwords
chmod 600 /etc/pyvisioniq/pyvisioniq.env
```

### 3. Network Security

- Use reverse proxy (nginx/Apache) with SSL
- Restrict access with firewall rules
- Consider VPN-only access for sensitive deployments

### 4. Using External Secret Managers

#### AWS Secrets Manager
```python
# In pyvisioniq.py, add:
import boto3

def get_master_password():
    if 'AWS_SECRET_NAME' in os.environ:
        client = boto3.client('secretsmanager')
        response = client.get_secret_value(SecretId=os.environ['AWS_SECRET_NAME'])
        return response['SecretString']
    return os.environ.get('PYVISIONIQ_MASTER_PASSWORD')
```

#### HashiCorp Vault
```bash
# Store password in Vault
vault kv put secret/pyvisioniq master_password=your_password

# In service, fetch dynamically
export PYVISIONIQ_MASTER_PASSWORD=$(vault kv get -field=master_password secret/pyvisioniq)
```

## Monitoring

### Health Checks

The `/metrics` endpoint provides Prometheus metrics:
```bash
curl http://localhost:8001/metrics
```

### Logging

- Systemd: `journalctl -u pyvisioniq-secure -f`
- Docker: `docker-compose logs -f`
- PM2: `pm2 logs pyvisioniq`

### Alerts

Set up monitoring with:
- Prometheus + AlertManager
- Grafana
- Uptime monitoring services

## Backup and Recovery

### Backup Strategy

1. Encrypted configuration:
   ```bash
   cp -r .pyvisioniq /backup/location/
   ```

2. Vehicle data:
   ```bash
   cp -r data /backup/location/
   ```

3. Automated backup script:
   ```bash
   #!/bin/bash
   BACKUP_DIR="/backup/pyvisioniq/$(date +%Y%m%d)"
   mkdir -p "$BACKUP_DIR"
   cp -r .pyvisioniq "$BACKUP_DIR/"
   cp -r data "$BACKUP_DIR/"
   ```

### Recovery

1. Stop the service
2. Restore files from backup
3. Verify permissions
4. Start the service

## Troubleshooting

### Service won't start

1. Check logs for errors
2. Verify master password is correct:
   ```bash
   PYVISIONIQ_MASTER_PASSWORD=test python pyvisioniq.py
   ```
3. Check file permissions
4. Ensure all dependencies are installed

### Can't decrypt configuration

1. Verify master password
2. Check if config.enc exists and is readable
3. Try re-initializing with `secure_config.py init`

### High memory/CPU usage

1. Check for runaway data collection
2. Verify update intervals
3. Monitor with: `htop` or `docker stats`

## Performance Tuning

### Systemd Limits

Add to service file:
```ini
[Service]
# Memory limit
MemoryLimit=512M
# CPU quota (50% of one core)
CPUQuota=50%
```

### Docker Limits

In docker-compose.yml:
```yaml
services:
  pyvisioniq:
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 512M
```

## Updates and Maintenance

### Updating the Application

1. Stop the service
2. Backup current installation
3. Update code:
   ```bash
   git pull
   pip install -r requirements.txt
   ```
4. Restart service

### Log Rotation

For systemd with journal:
```bash
# /etc/systemd/journald.conf
SystemMaxUse=100M
SystemKeepFree=15%
```

For file-based logs:
```bash
# /etc/logrotate.d/pyvisioniq
/var/log/pyvisioniq/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
}
```