#!/bin/bash
# Bee Tracker - Raspberry Pi Setup Script
# Run once on each Pi after flashing Raspberry Pi OS (64-bit)
# Usage: bash setup.sh

echo "=== Bee Tracker Setup ==="
echo ""

# System update
sudo apt-get update -y
sudo apt-get upgrade -y

# Camera dependencies
sudo apt-get install -y python3-picamera2 python3-libcamera

# Python packages
pip3 install opencv-contrib-python numpy pandas reportlab Pillow --break-system-packages

# Create data directories
mkdir -p ~/bee_tracker/data/verification_images/site_A
mkdir -p ~/bee_tracker/data/verification_images/site_B
mkdir -p ~/bee_tracker/tags

# Enable camera interface (Pi 4)
sudo raspi-config nonint do_camera 0

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Copy the scripts/ folder to ~/bee_tracker/scripts/"
echo "  2. Generate tags:  python3 scripts/generate_tags.py"
echo "  3. Run detector:   python3 scripts/detect.py --site A"
echo "  4. After fieldwork: python3 scripts/analyze.py"
echo ""
echo "To run detector automatically on boot, add to crontab:"
echo "  @reboot sleep 10 && python3 /home/pi/bee_tracker/scripts/detect.py --site A >> /home/pi/bee_tracker/data/log.txt 2>&1"
