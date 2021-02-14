#!/bin/bash

set -e

DIR="${OUTPUT:-config}"

ls ${DIR}/*.ips &> /dev/null || exit 0

cat << EOF
spec:
  template:
    spec:
      hostAliases:
EOF

for IPFILE in "$DIR"/*.ips; do
	HOSTNAME=$(basename "$IPFILE")
	while read -r IP; do
		echo "      - ip: \"$IP\""
		echo "        hostnames:"
		echo "        - ${HOSTNAME/.ips/}"
	done < "$IPFILE"
done
