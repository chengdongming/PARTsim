# Base development image
FROM gabrieleara/dev_environment:cpp

RUN apt-get update && DEBIAN_FRONTEND=noninteractive \
    apt-get install -y \
    meson \
    python3-pip \
 && rm -rf /var/lib/apt/lists/* \
 && pip3 install cpplint
