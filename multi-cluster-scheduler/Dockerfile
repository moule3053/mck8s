FROM python:3.7
RUN pip3 install kopf kubernetes pint prometheus-api-client pandas
COPY utils.py /utils.py
COPY multiclusterscheduler.py /multiclusterscheduler.py
CMD kopf run --standalone /multiclusterscheduler.py
