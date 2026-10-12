# cgp

## CollectD Graph Panel (CGP) on a Docker container

This repository contains a Dockerfile to build a Docker image with CollectD Graph Panel running on Apache and PHP.

The Docker image is available in [Docker Hub](https://hub.docker.com/r/pedbarbosa/cgp/).

### Docker compose usage example

```
services:
  cgp:
    image: pedbarbosa/cgp
    container_name: cgp
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - /var/lib/collectd/rrd:/var/lib/collectd/rrd:ro
    ports:
      - 80:80
    healthcheck:
      test: "curl -ILXGET http://localhost"
    restart: always
```

### Building and publishing

`./rebuild` builds, tests and publishes the image with [docker-image-refresh](https://gist.github.com/pedbarbosa/5a6ea23c8bcaf90f063eb0a082277d79), which runs `./test <image>` before tagging it `latest`.

The [Publish image](.github/workflows/publish.yml) workflow checks the published image daily and runs `./rebuild` when it's out of date: new commits here (other than docs, CI and the compose example) or in [CGP](https://github.com/pedbarbosa/CGP), a new `php:apache` base image, or Debian package updates. It can also be run by hand, with `force` to publish regardless. It needs a `DOCKERHUB_TOKEN` repository secret, a Docker Hub access token with Read, Write and Delete access so old tags can be pruned, and sends failures to the Slack incoming webhook in the `SLACK_WEBHOOK_URL` secret.
