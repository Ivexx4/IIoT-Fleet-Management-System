FROM alpine:3.21

RUN apk add --no-cache mosquitto openssl su-exec
COPY mosquitto.conf /mosquitto/config/mosquitto.conf
COPY aclfile /mosquitto/config/aclfile
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8883
ENTRYPOINT ["/entrypoint.sh"]
