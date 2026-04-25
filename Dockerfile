# Dockerfile for building FMCL Linux binaries with GLIBC compatibility
# Based on manylinux_2_28 (AlmaLinux 9, GLIBC 2.28) for broad compatibility
# Covers: Ubuntu 18.04+, Debian 10+, RHEL 8+, Fedora 33+
FROM quay.io/pypa/manylinux_2_28_x86_64

# Install system dependencies required by PyInstaller and tkinter
RUN dnf install -y \
    tk-devel \
    dbus-devel \
    libXScrnSaver-devel \
    libnotify-devel \
    nss-devel \
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
    && dnf clean all

# Install Python 3.11
RUN /opt/python/cp311-cp311/bin/python -m pip install --upgrade pip setuptools wheel

# Set Python 3.11 as default
ENV PATH="/opt/python/cp311-cp311/bin:${PATH}"
ENV PYTHON="/opt/python/cp311-cp311/bin/python3.11"

WORKDIR /app
