FROM php:apache
LABEL org.opencontainers.image.authors="Pedro Barbosa, https://github.com/pedbarbosa"

# CGP branch to build from, e.g. 'dev' for the dev image; empty for CGP's
# default branch, which 'latest' is built from
ARG CGP_BRANCH=

RUN \
    echo "**** update and upgrade packages ****" && \
    apt update && apt upgrade -y && \
    echo "**** install git and rrdtool ****" && \
    apt install -y git rrdtool && \
    echo "**** rewrite apache2 modules ****" && \
    a2enmod rewrite && \
    echo "**** pull CGP from git ****" && \
    git clone https://github.com/pedbarbosa/CGP.git /usr/src/CGP && \
    { [ -z "$CGP_BRANCH" ] || git -C /usr/src/CGP checkout "$CGP_BRANCH"; } && \
    git -C /usr/src/CGP rev-parse HEAD >/usr/src/CGP.revision && \
    rm -rf /usr/src/CGP/.git /var/www/html && \
    ln -s /usr/src/CGP /var/www/html && \
    echo "**** clean up ****" && \
    apt clean autoclean && \
    apt autoremove -y && \
    rm -rf /tmp/* && \
    rm -rf /usr/share/doc/* && \
    rm -rf /usr/share/info/* && \
    rm -rf /var/lib/{apt,cache,dpkg,log}/ && \
    rm -rf /var/tmp/* && \
    echo "**** let rrdtool, running as www-data, refresh the font cache ****" && \
    chown -R www-data:www-data /var/cache/fontconfig

COPY src/entrypoint.sh /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
