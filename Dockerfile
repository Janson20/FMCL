# Dockerfile for building FMCL Linux binaries with GLIBC compatibility
# Based on manylinux2014 (CentOS 7, GLIBC 2.17) for maximum compatibility
FROM quay.io/pypa/manylinux2014_x86_64

# Install system dependencies required by PyInstaller and tkinter
RUN yum install -y \
    tk-devel \
    dbus-devel \
    libXScrnSaver-devel \
    libnotify-devel \
    nss-devel \
    xorg-x11-server-Xvfb \
    fontconfig \
    cairo \
    pango \
    atk \
    gtk3 \
    alsa-lib \
    libffi-devel \
    openssl-devel \
    zlib-devel \
    bzip2-devel \
    xz-devel \
    readline-devel \
    sqlite-devel \
    gdbm-devel \
    && yum clean all

# Install Python 3.11
RUN /opt/python/cp311-cp311/bin/python -m pip install --upgrade pip setuptools wheel

# Set Python 3.11 as default
ENV PATH="/opt/python/cp311-cp311/bin:${PATH}"
ENV PYTHON="/opt/python/cp311-cp311/bin/python3.11"

WORKDIR /app
