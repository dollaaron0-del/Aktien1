#!/bin/bash
set -e
sed -i 's/--server.address=127.0.0.1/--server.address=0.0.0.0/' /etc/systemd/system/aktien_dashboard.service
systemctl daemon-reload
systemctl restart aktien_dashboard.service
echo "FERTIG"
