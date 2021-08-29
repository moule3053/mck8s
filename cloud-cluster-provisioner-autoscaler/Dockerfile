FROM python:3.7
RUN pip3 install kopf kubernetes pint PyYAML pandas
RUN apt-get update && apt -y install jq gettext-base
COPY helm /usr/local/bin/helm
RUN chmod +x /usr/local/bin/helm
COPY kubectl /usr/local/bin/kubectl
RUN chmod +x /usr/local/bin/kubectl
COPY kubefedctl /usr/local/bin/kubefedctl
RUN chmod +x /usr/local/bin/kubefedctl
COPY prom-remote.yaml /prom-remote.yaml
COPY metrics_server.yaml /metrics_server.yaml
COPY cilium.yaml /cilium.yaml
COPY cilium-etcd-external-nodeport.yaml /cilium-etcd-external-nodeport.yaml
COPY configure_cilium_1.sh /configure_cilium_1.sh
RUN chmod +x configure_cilium_1.sh
COPY configure_cilium_2.sh /configure_cilium_2.sh
RUN chmod +x configure_cilium_2.sh
COPY clustermesh-tools /clustermesh-tools
COPY utils.py /utils.py
COPY cloud_provisioner.py /cloud_provisioner.py
CMD kopf run --standalone /cloud_provisioner.py
