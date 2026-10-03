#!/bin/bash
# ============================================================
# DicomBridge - Linux user-local install (no sudo needed):
#   dist/DicomBridge -> ~/.local/bin/
#   desktop entry    -> ~/.local/share/applications/
#   icon             -> ~/.local/share/icons/
# Usage: ./install.sh
# Uninstall: rm ~/.local/bin/DicomBridge \
#   ~/.local/share/applications/DicomBridge.desktop \
#   ~/.local/share/icons/dicombridge.png
# ============================================================
set -e
cd "$(dirname "$0")"

BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons"
mkdir -p "$BIN" "$APPS" "$ICONS"

cp dist/DicomBridge "$BIN/DicomBridge"
chmod +x "$BIN/DicomBridge"
cp assets/logo.png "$ICONS/dicombridge.png"

cat > "$APPS/DicomBridge.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=DicomBridge
Comment=DICOM to PACS gateway
Exec=$BIN/DicomBridge
Icon=$ICONS/dicombridge.png
Categories=Utility;Medical;
Terminal=false
EOF

echo "DONE. Run: DicomBridge  (make sure ~/.local/bin is in PATH)"
