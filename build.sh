#!/bin/bash
# Сборка .deb-пакета AWG Manager.
set -euo pipefail
cd "$(dirname "$0")"

VERSION="${1:-$(grep -oP '(?<=^VERSION = ")[^"]+' src/awgmanager/app.py)}"
BUILD=debian-build
PKG="awg-manager_${VERSION}_all.deb"

echo ">> Сборка версии $VERSION"
rm -rf "$BUILD"
mkdir -p "$BUILD/DEBIAN"

sed -e "s/@VERSION@/$VERSION/" packaging/control.in > "$BUILD/DEBIAN/control"
install -Dm755 packaging/postinst "$BUILD/DEBIAN/postinst"
install -Dm755 packaging/postrm   "$BUILD/DEBIAN/postrm"

install -Dm755 helper/awg-helper "$BUILD/usr/libexec/awg-manager/awg-helper"
install -Dm755 helper/awg-split  "$BUILD/usr/libexec/awg-manager/awg-split"
for b in awg-manager awg-manager-tray awg-run-direct awg-setup-direct awg-manager-doctor; do
    install -Dm755 "bin/$b" "$BUILD/usr/bin/$b"
done

install -Dm644 data/polkit/org.awgmanager.policy       "$BUILD/usr/share/polkit-1/actions/org.awgmanager.policy"
install -Dm644 data/polkit/49-awg-manager.rules        "$BUILD/usr/share/polkit-1/rules.d/49-awg-manager.rules"
install -Dm644 data/applications/awg-manager.desktop   "$BUILD/usr/share/applications/awg-manager.desktop"
install -Dm644 data/autostart/awg-manager-tray.desktop "$BUILD/etc/xdg/autostart/awg-manager-tray.desktop"
install -Dm644 data/icons/hicolor/scalable/apps/awg-manager.svg \
               "$BUILD/usr/share/icons/hicolor/scalable/apps/awg-manager.svg"
install -Dm644 LICENSE "$BUILD/usr/share/doc/awg-manager/copyright"

mkdir -p "$BUILD/usr/share/awg-manager/awgmanager"
install -m644 src/awgmanager/*.py "$BUILD/usr/share/awg-manager/awgmanager/"

find "$BUILD" -type d -exec chmod 755 {} \;
dpkg-deb --build --root-owner-group "$BUILD" "$PKG"
echo ">> Готово: $PKG"
dpkg-deb -I "$PKG" | head -12
