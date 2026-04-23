FROM postgres:16-alpine AS base

RUN apk add --no-cache build-base postgresql-dev git && \
    git clone --depth 1 --branch v1.6.0 https://github.com/fboulnois/pg_uuidv7.git /tmp/pg_uuidv7 && \
    cd /tmp/pg_uuidv7 && make && make install && \
    rm -rf /tmp/pg_uuidv7 && apk del build-base postgresql-dev git
