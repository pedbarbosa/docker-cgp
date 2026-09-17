FROM php:apache
LABEL org.opencontainers.image.authors="Pedro Barbosa, https://github.com/pedbarbosa"

RUN \
    echo "**** update and upgrade packages ****" && \
    apt update && apt upgrade -y && \
    echo "**** install git and rrdtool ****" && \
    apt install -y git rrdtool && \
    echo "**** rewrite apache2 modules ****" && \
    a2enmod rewrite && \
    echo "**** pull CGP from git ****" && \
    git clone https://github.com/pedbarbosa/CGP.git /usr/src/CGP && \
    rm -rf /usr/src/CGP/.git /var/www/html && \
    ln -s /usr/src/CGP /var/www/html && \
    echo "**** clean up ****" && \
    apt clean autoclean && \
    apt autoremove -y && \
    rm -rf /tmp/* && \
    rm -rf /usr/share/doc/* && \
    rm -rf /usr/share/info/* && \
    rm -rf /var/lib/{apt,cache,dpkg,log}/ && \
    rm -rf /var/tmp/*

COPY src/entrypoint.sh /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
