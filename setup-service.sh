#!/bin/bash
# Setup script for PyVisionIQ service with encrypted credentials

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}PyVisionIQ Service Setup${NC}"
echo "=========================="

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}This script must be run as root${NC}"
   exit 1
fi

# Configuration
SERVICE_USER="pyvisioniq"
SERVICE_GROUP="pyvisioniq"
INSTALL_DIR="/opt/pyvisioniq"
CONFIG_DIR="/etc/pyvisioniq"
DATA_DIR="${INSTALL_DIR}/data"
VENV_DIR="${INSTALL_DIR}/venv"

echo -e "${YELLOW}1. Creating service user and directories...${NC}"

# Create service user if not exists
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --home-dir "$INSTALL_DIR" --shell /bin/false "$SERVICE_USER"
    echo -e "${GREEN}Created service user: $SERVICE_USER${NC}"
else
    echo "Service user already exists"
fi

# Create directories
mkdir -p "$INSTALL_DIR"
mkdir -p "$CONFIG_DIR"
mkdir -p "$DATA_DIR"
mkdir -p "${INSTALL_DIR}/.pyvisioniq"
mkdir -p "${DATA_DIR}/data_archive"

echo -e "${YELLOW}2. Copying application files...${NC}"

# Copy application files
cp -r *.py "$INSTALL_DIR/"
cp -r templates "$INSTALL_DIR/" 2>/dev/null || true
cp requirements.txt "$INSTALL_DIR/"

# Set up virtual environment
echo -e "${YELLOW}3. Setting up Python virtual environment...${NC}"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

echo -e "${YELLOW}4. Setting up encrypted configuration...${NC}"

# Check if encrypted config exists
if [ -f "${INSTALL_DIR}/.pyvisioniq/config.enc" ]; then
    echo -e "${GREEN}Encrypted configuration already exists${NC}"
else
    echo -e "${YELLOW}No encrypted configuration found. Setting up now...${NC}"
    
    # Run secure_config.py to initialize
    cd "$INSTALL_DIR"
    sudo -u "$SERVICE_USER" "$VENV_DIR/bin/python" secure_config.py init
fi

echo -e "${YELLOW}5. Creating environment file for systemd...${NC}"

# Create environment file
cat > "$CONFIG_DIR/pyvisioniq.env" << EOF
# PyVisionIQ Environment Configuration
# WARNING: This file contains sensitive information. Protect it carefully!

# Master password for encrypted configuration
# You must set this to the password you used during secure_config.py init
PYVISIONIQ_MASTER_PASSWORD=CHANGE_ME_TO_YOUR_MASTER_PASSWORD

# Optional: Override default paths
# PYVISIONIQ_CONFIG_FILE=/opt/pyvisioniq/.pyvisioniq/config.enc
# PYVISIONIQ_CSV_FILE=/opt/pyvisioniq/data/vehicle_data.csv

# Optional: Port configuration
# PYVISIONIQ_PORT=8001
# PYVISIONIQ_HOST=0.0.0.0
EOF

chmod 600 "$CONFIG_DIR/pyvisioniq.env"
chown root:root "$CONFIG_DIR/pyvisioniq.env"

echo -e "${RED}IMPORTANT: Edit $CONFIG_DIR/pyvisioniq.env and set PYVISIONIQ_MASTER_PASSWORD${NC}"

echo -e "${YELLOW}6. Setting permissions...${NC}"

# Set ownership
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR"
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$DATA_DIR"

# Set permissions
chmod 755 "$INSTALL_DIR"
chmod 700 "${INSTALL_DIR}/.pyvisioniq"
chmod 600 "${INSTALL_DIR}/.pyvisioniq/config.enc" 2>/dev/null || true
chmod 755 "$DATA_DIR"

echo -e "${YELLOW}7. Installing systemd service...${NC}"

# Copy service file
cp systemd/pyvisioniq-secure.service /etc/systemd/system/

# Reload systemd
systemctl daemon-reload

echo -e "${GREEN}Setup complete!${NC}"
echo
echo "Next steps:"
echo "1. Edit $CONFIG_DIR/pyvisioniq.env and set your master password"
echo "2. Test the configuration:"
echo "   sudo -u $SERVICE_USER PYVISIONIQ_MASTER_PASSWORD=yourpassword $VENV_DIR/bin/python $INSTALL_DIR/pyvisioniq.py"
echo "3. Enable and start the service:"
echo "   systemctl enable pyvisioniq-secure"
echo "   systemctl start pyvisioniq-secure"
echo "4. Check status:"
echo "   systemctl status pyvisioniq-secure"
echo "   journalctl -u pyvisioniq-secure -f"
echo
echo "The service will be available at http://localhost:8001"