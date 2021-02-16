FROM python:3.7
RUN pip3 install kopf kubernetes pint prometheus-api-client
COPY utils.py /utils.py
COPY multiclustershceduling.py /multiclustershceduling.py
CMD kopf run --standalone /multiclustershceduling.py
