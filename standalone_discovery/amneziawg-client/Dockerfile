# AmneziaWG-клиент (userspace, без ядерного модуля) — собран из официальных
# исходников amnezia-vpn, без сторонних Docker-образов.
# Источники: https://github.com/amnezia-vpn/amneziawg-go
#            https://github.com/amnezia-vpn/amneziawg-tools

# Alpine вместо Debian — маленький VPS, экономим место на диске при сборке.
FROM golang:1.25-alpine AS awggo-build
RUN apk add --no-cache git make
WORKDIR /src
RUN git clone --depth 1 https://github.com/amnezia-vpn/amneziawg-go.git .
RUN make

FROM alpine:3.20 AS awgtools-build
# bash обязателен здесь: Makefile включает WITH_WGQUICK, только если находит bash
# в системе сборки — без него awg-quick молча не соберётся.
RUN apk add --no-cache git make gcc musl-dev linux-headers libmnl-dev pkgconfig bash
WORKDIR /src
RUN git clone --depth 1 https://github.com/amnezia-vpn/amneziawg-tools.git .
WORKDIR /src/src
# WITH_WGQUICK автоопределяется по наличию bash строго в $(BINDIR)/bash (=/usr/bin/bash);
# в Alpine bash лежит в /bin/bash, автоопределение не срабатывает — форсируем явно.
RUN make && make install DESTDIR=/out PREFIX=/usr WITH_WGQUICK=yes WITH_BASHCOMPLETION=no WITH_SYSTEMDUNITS=no
RUN ls -la /out/usr/bin

FROM alpine:3.20
RUN apk add --no-cache iproute2 iptables bash libmnl ca-certificates openresolv

COPY --from=awggo-build /src/amneziawg-go /usr/bin/amneziawg-go
COPY --from=awgtools-build /out/usr/bin/awg /usr/bin/awg
COPY --from=awgtools-build /out/usr/bin/awg-quick /usr/bin/awg-quick
RUN chmod +x /usr/bin/amneziawg-go /usr/bin/awg /usr/bin/awg-quick \
    # Совместимость: существующие скрипты (wg_split_tunnel_add_domain.sh на n8n-хосте)
    # дёргают `docker exec <vpn> wg set ...` — awg полностью совместим по CLI с wg.
    && ln -sf /usr/bin/awg /usr/bin/wg \
    && ln -sf /usr/bin/awg-quick /usr/bin/wg-quick

ENV WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

HEALTHCHECK --interval=15s --timeout=10s --retries=12 --start-period=30s \
  CMD awg show wg0 > /dev/null 2>&1 || exit 1

ENTRYPOINT ["/entrypoint.sh"]
