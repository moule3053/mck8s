#!/bin/bash

set -e

NAMESPACE="${NAMESPACE:-kube-system}"
SERVICE_NAME="${SERVICE_NAME:-cilium-etcd-external}"
DIR="${OUTPUT:-config}"
KUBECONFIG="${KUBECONFIG:+--kubeconfig=${KUBECONFIG}}"
CILIUM_CONFIG="${CILIUM_CONFIG:-cilium-config}"
CILIUM_ETCD_SECRET="${CILIUM_ETCD_SECRET:-cilium-etcd-secrets}"

kubectl="kubectl ${KUBECONFIG}"

if [ -z "$CLUSTER_NAME" ]; then
	CM_NAME=$($kubectl -n "$NAMESPACE" get cm cilium-config -o json | jq -r -c '.data."cluster-name"')
	if [[ "$CM_NAME" != "" && "$CM_NAME" != "default" ]]; then
		echo "Derived cluster-name $CM_NAME from present ConfigMap"
		CLUSTER_NAME="$CM_NAME"
	else
		echo "CLUSTER_NAME is not set"
		echo "Set CLUSTER_NAME to the name of the cluster"
		exit 1
	fi
fi

mkdir -p "$DIR"

SECRETS=$($kubectl -n "$NAMESPACE" get secret "$CILIUM_ETCD_SECRET" -o json | jq -c '.data | to_entries[]')
for SECRET in $SECRETS; do
  KEY=$(echo "$SECRET" | jq -r '.key')
  echo "$SECRET" | jq -r '.value' | base64 --decode > "$DIR/$CLUSTER_NAME.$KEY"
done

SERVICE=$($kubectl -n "$NAMESPACE" get svc "$SERVICE_NAME" -o json)
SERVICE_TYPE=$(echo "$SERVICE" | jq -r -c '.spec.type')

case "$SERVICE_TYPE" in
"NodePort")
	# Grab the node's internal IPs.
	IPS=$($kubectl -n "$NAMESPACE" get node \
		-o jsonpath='{.items[*].status.addresses[?(@.type=="InternalIP")].address}' | tr ' ' '\n')
	# Grab the node port on which etcd is exposed.
	PORT=$($kubectl -n "$NAMESPACE" get svc "$SERVICE_NAME" \
		-o jsonpath='{.spec.ports[?(@.port==2379)].nodePort}')
	;;
"LoadBalancer")
	# Grab the load-balancer's IP(s).
	IPS=$(echo "$SERVICE" | jq -r -c '.status.loadBalancer.ingress[0].ip')
	if [ -z "$IPS" ] || [ "$IPS" = null ]; then
		HOSTNAME=$(echo "$SERVICE" | jq -r -c '.status.loadBalancer.ingress[0].hostname')
		if [ -z "$HOSTNAME" ] || [ "$HOSTNAME" == null ]; then
			echo "Unable to determine hostname for service $SERVICE_NAME. .status.loadBalancer.ingress[0].hostname is empty"
			exit 1
		fi
		IPS=$(host "$HOSTNAME" | grep address | awk '{print $NF}')
		if [ -z "$IPS" ]; then
			echo "Unable to resolve hostname $HOSTNAME to IP"
			exit 1
		fi
	fi
	# Use '2379' as the port, as that's what load-balancers will be using.
	PORT="2379"
	;;
*)
	echo "Services of type $SERVICE_TYPE are not supported. Please use NodePort or LoadBalancer."
	exit 1
	;;
esac

ETCD_CONFIG=$($kubectl -n "$NAMESPACE" get cm "$CILIUM_CONFIG" -o jsonpath='{.data.etcd-config}')
rm -f "$DIR/$CLUSTER_NAME"

# If we are using addressable IPs then we can use the config as is.
for ip in $IPS; do
	[[ "$ETCD_CONFIG" =~ "$ip" ]] && echo "$ETCD_CONFIG" > "$DIR/$CLUSTER_NAME" && break
done

# Otherwise, use well known cilium subdomain
if [ ! -e "$DIR/$CLUSTER_NAME" ]; then
	SERVICE_NAME="${CLUSTER_NAME}.mesh.cilium.io"
	echo "$IPS"  > "$DIR/${SERVICE_NAME}.ips"

	cat > "$DIR/$CLUSTER_NAME" << EOF
endpoints:
- https://${SERVICE_NAME}:${PORT}
EOF

	echo "$ETCD_CONFIG"                                                  \
		| grep -E "(ca|key|cert)-file:"                              \
		| sed "s|/.*/|/var/lib/cilium/clustermesh/${CLUSTER_NAME}.|" \
	>> "$DIR/$CLUSTER_NAME"
fi

echo "===================================================="
echo " WARNING: The directory $DIR contains private keys."
echo "          Delete after use."
echo "===================================================="
