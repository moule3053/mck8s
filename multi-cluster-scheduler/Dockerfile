FROM python:3.7
RUN pip3 install kopf kubernetes pint prometheus-api-client pandas
COPY serf /usr/local/bin/serf
RUN chmod +x /usr/local/bin/serf
COPY utils.py /utils.py
COPY multiclusterscheduler.py /multiclusterscheduler.py
CMD kopf run --standalone /multiclusterscheduler.py
