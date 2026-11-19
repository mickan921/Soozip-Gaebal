FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
ARG QA_UID=1000
ARG QA_GID=1000
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pyatspi python3-gi at-spi2-core dbus-x11 \
    xvfb openbox xdotool x11-utils scrot fonts-dejavu-core pkexec \
    libegl1 libopengl0 libgl1 libxkbcommon0 libxkbcommon-x11-0 libdbus-1-3 \
    libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
    libxcb-xinerama0 libxcb-xkb1 libxcb-shape0 libxcb-randr0 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && (userdel -r ubuntu 2>/dev/null || true) \
    && groupadd -g "$QA_GID" qa \
    && useradd -m -u "$QA_UID" -g qa -s /bin/bash qa
ENV DISPLAY=:99 QT_QPA_PLATFORM=xcb QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 QT_ACCESSIBILITY=1 XDG_SESSION_TYPE=x11
USER qa
WORKDIR /home/qa
CMD ["sleep", "infinity"]
