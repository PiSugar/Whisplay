#!/bin/bash

if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root (use sudo)" 1>&2
   exit 1
fi

is_Raspberry=$(cat /proc/device-tree/model | awk  '{print $1}')
if [ "x${is_Raspberry}" != "xRaspberry" ] ; then
  echo "Sorry, this drivers only works on raspberry pi"
  exit 1
fi
#Update the kernel
#sudo apt-get update -y
#sudo apt-get full-upgrade -y
#sudo apt-get install --reinstall alsa-base alsa-utils -y

enable_spi() {
    echo "Attempting to enable SPI interface..."
    if which raspi-config > /dev/null 2>&1; then
        # Strictly use raspi-config to enable SPI
        sudo raspi-config nonint do_spi 0
        echo "SPI interface enabled successfully via raspi-config."
    else
        # raspi-config not found, show error and exit
        echo "ERROR: raspi-config not found. Cannot automatically enable SPI interface." 1>&2
        echo "Please install raspi-config or enable SPI manually before running." 1>&2
        exit 1
    fi
}

enable_spi
# Extract local archive
unzip -o WM8960-Audio-HAT.zip
cd WM8960-Audio-HAT

# install dtbos
#cp wm8960-soundcard.dtbo /boot/overlays


#set kernel moduels
grep -q "i2c-dev" /etc/modules || \
  echo "i2c-dev" >> /etc/modules  
grep -q "snd-soc-wm8960" /etc/modules || \
  echo "snd-soc-wm8960" >> /etc/modules  
grep -q "snd-soc-wm8960-soundcard" /etc/modules || \
  echo "snd-soc-wm8960-soundcard" >> /etc/modules  
  
#set dtoverlays
sed -i -e 's:#dtparam=i2s=on:dtparam=i2s=on:g'  /boot/firmware/config.txt || true
sed -i -e 's:#dtparam=i2c_arm=on:dtparam=i2c_arm=on:g'  /boot/firmware/config.txt || true
grep -q "dtoverlay=i2s-mmap" /boot/firmware/config.txt || \
  echo "dtoverlay=i2s-mmap" >> /boot/firmware/config.txt

grep -q "dtparam=i2s=on" /boot/firmware/config.txt || \
  echo "dtparam=i2s=on" >> /boot/firmware/config.txt

grep -q "dtoverlay=wm8960-soundcard" /boot/firmware/config.txt || \
  echo "dtoverlay=wm8960-soundcard" >> /boot/firmware/config.txt
  
#install config files
mkdir /etc/wm8960-soundcard || true
cp *.conf /etc/wm8960-soundcard
cp *.state /etc/wm8960-soundcard

#set service 
cp wm8960-soundcard /usr/bin/
cp wm8960-soundcard.service /lib/systemd/system/
sudo chmod u=rwx,go=rx /usr/bin/wm8960-soundcard

systemctl enable  wm8960-soundcard.service 
systemctl start wm8960-soundcard                                



echo "------------------------------------------------------"
echo "Please reboot your raspberry pi to apply all settings"
echo "Enjoy!"
echo "------------------------------------------------------"