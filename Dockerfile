FROM python:3
WORKDIR /environment

RUN apt-get update
RUN pip install --upgrade pip

COPY ./requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY . /environment
CMD python main.py
