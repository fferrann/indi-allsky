#!/bin/bash

#set -x  # command tracing
set -o errexit
set -o nounset

PATH=/usr/local/bin:/usr/bin:/bin
export PATH


function catch_error() {
    echo
    echo
    echo "###############"
    echo "###  ERROR  ###"
    echo "###############"
    echo
    echo "The script exited abnormally, please try to run again..."
    echo
    echo
    exit 1
}
trap catch_error ERR

function catch_sigint() {
    echo
    echo
    echo "###############"
    echo "###  ERROR  ###"
    echo "###############"
    echo
    echo "The script was interrupted, please run the script again to finish..."
    echo
    echo
    exit 1
}
trap catch_sigint SIGINT


if [ ! -f "/etc/os-release" ]; then
    echo
    echo "Unable to determine OS from /etc/os-release"
    echo
    exit 1
fi

source /etc/os-release


DISTRO_ID="${ID:-unknown}"
DISTRO_VERSION_ID="${VERSION_ID:-unknown}"
CPU_ARCH=$(uname -m)
CPU_BITS=$(getconf LONG_BIT)
CPU_TOTAL=$(grep -c "^proc" /proc/cpuinfo)
MEM_TOTAL=$(grep MemTotal /proc/meminfo | awk "{print \$2}")


if [ -f "/proc/device-tree/model" ]; then
    SYSTEM_MODEL=$(cat /proc/device-tree/model)
else
    SYSTEM_MODEL="Generic PC"
fi


if which indiserver >/dev/null 2>&1; then
    INDISERVER=$(which indiserver)
else
    INDISERVER="not found"
fi


SCRIPT_DIR=$(dirname "$0")
cd "$SCRIPT_DIR/.."
ALLSKY_DIRECTORY=$PWD
cd "$OLDPWD"


echo "#################################"
echo "### indi-allsky support info  ###"
echo "#################################"

sleep 3

echo
echo "Distribution: $DISTRO_ID"
echo "Release: $DISTRO_VERSION_ID"
echo "Arch: $CPU_ARCH"
echo "Bits: $CPU_BITS"
echo
echo "CPUs: $CPU_TOTAL"
echo "Memory: $MEM_TOTAL kB"
echo
echo "System: $SYSTEM_MODEL"
echo
echo "Uptime"
uptime
echo
echo "Filesystems"
df -k
echo
echo "system python: $(python3 -V)"
echo
echo "indiserver: $INDISERVER"
echo


if [ -f "/etc/astroberry.version" ]; then
    echo "Detected Astroberry server"
    echo
fi


echo
echo "User info"
id
echo

echo "Process info"
# shellcheck disable=SC2009
ps auxwww | grep indi | grep -v grep || true
echo

echo "USB info"
lsusb
echo

echo "USB Permissions"
find /dev/bus/usb -ls || true
echo

echo "video device Permissions"
ls -l /dev/video* || true
echo

echo "Module info"
lsmod
echo


echo "git status"
git status | head -n 100
echo


echo "git log"
git log -n 1 | head -n 100
echo


if pkg-config --exists libindi; then
    DETECTED_INDIVERSION=$(pkg-config --modversion libindi)
    echo "indi version: $DETECTED_INDIVERSION"
    echo
else
    echo "indi version: not detected"
    echo
fi


echo "indi packages"
dpkg -l | grep libindi || true
echo


if which indi_getprop >/dev/null 2>&1; then
    echo "Detected indi properties"
    indi_getprop -v 2>&1 || true
    echo
fi


if pkg-config --exists libcamera; then
    DETECTED_LIBCAMERA=$(pkg-config --modversion libcamera)
    echo "libcamera version: $DETECTED_LIBCAMERA"
    echo
else
    echo "libcamera: not detected"
    echo
fi


echo "libcamera packages"
dpkg -l | grep libcamera || true
echo

echo "libcamera cameras"
if which rpicam-hello >/dev/null 2>&1; then
    echo "rpicam-hello: $(which rpicam-hello)"
    rpicam-hello --list-cameras || true
    echo
elif which libcamera-hello >/dev/null 2>&1; then
    echo "libcamera-hello: $(which libcamera-hello)"
    libcamera-hello --list-cameras || true
    echo
else
    echo "libcamera-hello not available"
    echo
fi


echo "python packages"
dpkg -l | grep python || true
echo


if [ -d "${ALLSKY_DIRECTORY}/virtualenv/indi-allsky" ]; then
    echo "Detected indi-allsky virtualenv"

    # shellcheck source=/dev/null
    source "${ALLSKY_DIRECTORY}/virtualenv/indi-allsky/bin/activate"
    echo "virtualenv python: $(python3 -V)"
    echo "virtualenv PATH: $PATH"

    if which flask >/dev/null 2>&1; then
        echo "flask command: $(which flask)"
    else
        echo "flask: not found"
    fi

    echo
    echo "virtualenv python modules"
    pip freeze
    deactivate
    echo
else
    echo "indi-allsky virtualenv is not created"
    echo
fi

echo "#################################"
echo "###     end support info      ###"
echo "#################################"
