#!/bin/sh
# Bouwt een SD-kaart-image: Raspberry Pi OS Lite met p1meter voorgeinstalleerd.
#
#   sudo sh image/build.sh [uitvoer.img]
#
# Nodig: curl, xz, mount (loop), python3 met pip. Er wordt niets in de
# image uitgevoerd, dus dit werkt op elke Linux-machine (ook x86).
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:-$REPO/build/p1meter-raspios-lite.img}
# 32-bit Lite draait op elk Raspberry Pi-model, ook de Zero en de 1.
BASE_URL=${BASE_URL:-https://downloads.raspberrypi.com/raspios_lite_armhf_latest}
WORK=$(dirname "$OUT")
MNT=$WORK/mnt

mkdir -p "$WORK" "$MNT"
if [ ! -f "$WORK/base.img.xz" ]; then
    echo "Raspberry Pi OS downloaden..."
    curl -fsSL --retry 3 -o "$WORK/base.img.xz.part" "$BASE_URL"
    mv "$WORK/base.img.xz.part" "$WORK/base.img.xz"
fi
echo "Uitpakken..."
xz -dc "$WORK/base.img.xz" > "$OUT"

# Tweede partitie = root-bestandssysteem
# (startsector uit de MBR-partitietabel: tweede regel, bytes 8-11)
START=$(python3 -c 'import struct, sys
mbr = open(sys.argv[1], "rb").read(512)
print(struct.unpack_from("<I", mbr, 446 + 16 + 8)[0])' "$OUT")
[ "$START" -gt 0 ] || { echo "Root-partitie niet gevonden" >&2; exit 1; }

cleanup() { mountpoint -q "$MNT" && umount "$MNT"; rmdir "$MNT" 2>/dev/null || true; }
trap cleanup EXIT
mount -o loop,offset=$((START * 512)) "$OUT" "$MNT"

echo "p1meter installeren..."
install -d "$MNT/opt/p1meter" "$MNT/etc/p1meter"
cp -r "$REPO/p1meter" "$MNT/opt/p1meter/"
find "$MNT/opt/p1meter" -name __pycache__ -prune -exec rm -rf {} +
# pyserial is pure Python, dus de wheel van de bouwmachine werkt ook op de Pi
python3 -m pip install --quiet --no-compile --no-deps --target "$MNT/opt/p1meter/vendor" pyserial

sed 's|^database = .*|database = "/var/lib/p1meter/p1meter.db"|' \
    "$REPO/config.example.toml" > "$MNT/etc/p1meter/config.toml"
grep -q '^database = "/var/lib/p1meter/p1meter.db"' "$MNT/etc/p1meter/config.toml"

install -m 644 "$REPO/image/p1meter.service" "$MNT/etc/systemd/system/p1meter.service"
ln -sf ../p1meter.service "$MNT/etc/systemd/system/multi-user.target.wants/p1meter.service"

# Dashboard bereikbaar als http://p1meter.local:8080, dagtotalen in NL-tijd
echo p1meter > "$MNT/etc/hostname"
sed -i 's/^127\.0\.1\.1.*/127.0.1.1\tp1meter/' "$MNT/etc/hosts"
ln -sf /usr/share/zoneinfo/Europe/Amsterdam "$MNT/etc/localtime"
echo Europe/Amsterdam > "$MNT/etc/timezone"

chown -R 0:0 "$MNT/opt/p1meter" "$MNT/etc/p1meter"
umount "$MNT"
echo "Klaar: $OUT"
