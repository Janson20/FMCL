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

# Rebuild Python 3.11 with --enable-shared (required by PyInstaller)
# manylinux Python is statically linked, PyInstaller needs shared libpython
ARG PYTHON_VERSION=3.11.15
RUN PYTHON_VERSION_NUM=$(/opt/python/cp311-cp311/bin/python3.11 -c "import sys; print(sys.version.split()[0])") && \
    curl -sS https://www.python.org/ftp/python/${PYTHON_VERSION_NUM}/Python-${PYTHON_VERSION_NUM}.tar.xz -o /tmp/Python.tar.xz && \
    tar xf /tmp/Python.tar.xz -C /tmp && \
    cd /tmp/Python-${PYTHON_VERSION_NUM} && \
    ./configure --prefix=/opt/python/cp311-cp311 --enable-shared --with-ensurepip=install \
        LDFLAGS="-Wl,-rpath,/opt/python/cp311-cp311/lib" && \
    make -j$(nproc) && \
    make install && \
    rm -rf /tmp/Python.tar.xz /tmp/Python-${PYTHON_VERSION_NUM}

# Install Python 3.11 packages
RUN /opt/python/cp311-cp311/bin/python -m pip install --upgrade pip setuptools wheel

# Set Python 3.11 as default
ENV PATH="/opt/python/cp311-cp311/bin:${PATH}"
ENV LD_LIBRARY_PATH="/opt/python/cp311-cp311/lib:${LD_LIBRARY_PATH}"
ENV PYTHON="/opt/python/cp311-cp311/bin/python3.11"

WORKDIR /app
