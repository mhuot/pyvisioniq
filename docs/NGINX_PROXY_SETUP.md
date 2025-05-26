# PyVisionic with nginx-proxy Network Setup

This guide explains how to run PyVisionic with the nginx-proxy network for automatic SSL and reverse proxy configuration.

## Prerequisites

1. [nginx-proxy](https://github.com/nginx-proxy/nginx-proxy) running on your Docker host
2. nginx-proxy-network created and nginx-proxy connected to it
3. (Optional) [acme-companion](https://github.com/nginx-proxy/acme-companion) for automatic Let's Encrypt SSL

## Quick Setup

### 1. Ensure nginx-proxy network exists

```bash
# Check if network exists
docker network ls | grep nginx-proxy-network

# If not, create it
docker network create nginx-proxy-network
```

### 2. Configure PyVisionic

Update your `.env` file with the nginx-proxy settings:

```env
# Your domain name for PyVisionic
VIRTUAL_HOST=pyvisionic.yourdomain.com

# Port should match the internal PORT setting
VIRTUAL_PORT=5000

# For automatic SSL with Let's Encrypt (optional)
LETSENCRYPT_HOST=pyvisionic.yourdomain.com
LETSENCRYPT_EMAIL=your-email@example.com
```

### 3. Start PyVisionic

```bash
# Build and start
docker compose up --build -d

# Check logs
docker compose logs -f
```

## How It Works

1. **No Port Mapping**: The container uses `expose` instead of `ports`, making it accessible only through nginx-proxy
2. **Automatic Discovery**: nginx-proxy detects the container via Docker labels (VIRTUAL_HOST)
3. **SSL Termination**: If using acme-companion, SSL is automatically configured
4. **Network Isolation**: PyVisionic is only accessible through the reverse proxy

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `VIRTUAL_HOST` | Domain name for nginx-proxy | `pyvisionic.example.com` |
| `VIRTUAL_PORT` | Internal port (must match PORT) | `5000` |
| `LETSENCRYPT_HOST` | Domain for SSL certificate | `pyvisionic.example.com` |
| `LETSENCRYPT_EMAIL` | Email for Let's Encrypt | `admin@example.com` |

## Using Custom SSL Certificates

If you're using custom SSL certificates (like *.geekpark.com) with nginx-proxy:

1. Place your certificates in nginx-proxy's certs directory:
   ```bash
   # Typically /var/docker/nginx-proxy/certs/
   cp fullchain.pem /var/docker/nginx-proxy/certs/pyvisionic.geekpark.com.crt
   cp privkey.pem /var/docker/nginx-proxy/certs/pyvisionic.geekpark.com.key
   ```

2. Set VIRTUAL_HOST to match your certificate domain:
   ```env
   VIRTUAL_HOST=pyvisionic.geekpark.com
   ```

## Troubleshooting

### Container not accessible

1. Verify nginx-proxy can see the container:
   ```bash
   docker exec nginx-proxy cat /etc/nginx/conf.d/default.conf | grep pyvisionic
   ```

2. Check if container is on the correct network:
   ```bash
   docker inspect pyvisionic | grep -A 10 Networks
   ```

3. Ensure nginx-proxy is also on the same network:
   ```bash
   docker network inspect nginx-proxy-network
   ```

### SSL not working

1. Check acme-companion logs:
   ```bash
   docker logs acme-companion
   ```

2. Verify DNS is pointing to your server
3. Ensure ports 80 and 443 are accessible

### Multiple PyVisionic Instances

To run multiple instances on the same host:

1. Use different container names
2. Set unique VIRTUAL_HOST for each
3. Each instance needs its own data directories

Example:
```yaml
services:
  pyvisionic-home:
    container_name: pyvisionic-home
    environment:
      - VIRTUAL_HOST=home.pyvisionic.example.com
    # ... rest of config

  pyvisionic-work:
    container_name: pyvisionic-work
    environment:
      - VIRTUAL_HOST=work.pyvisionic.example.com
    # ... rest of config
```

## Security Considerations

1. **No Direct Access**: Containers are not exposed on host ports
2. **SSL Only**: Configure nginx-proxy to redirect HTTP to HTTPS
3. **Network Isolation**: Only containers on nginx-proxy-network can communicate
4. **Headers**: nginx-proxy automatically adds security headers

## Reverting to Standalone Mode

To run PyVisionic without nginx-proxy:

1. Stop the container:
   ```bash
   docker compose down
   ```

2. Modify docker-compose.yml:
   - Change `expose` back to `ports`
   - Remove the networks section
   - Remove VIRTUAL_* environment variables

3. Restart:
   ```bash
   docker compose up -d
   ```