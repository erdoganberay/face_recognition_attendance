FROM python:3.10-slim-bullseye

RUN apt-get update && \
    apt-get upgrade -y

RUN apt-get install -y --fix-missing \
    build-essential \
    cmake \
    gfortran \
    git \
    wget \
    curl \
    graphicsmagick

RUN apt-get install -y --fix-missing \
    libgraphicsmagick1-dev \
    libatlas-base-dev \
    libavcodec-dev \
    libavformat-dev \
    libboost-all-dev \
    libgtk2.0-dev \
    libjpeg-dev \
    liblapack-dev \
    libswscale-dev

RUN apt-get install -y --fix-missing pkg-config \
    python3-dev \
    python3-numpy \
    software-properties-common \
    zip \
    && apt-get clean && rm -rf /tmp/* /var/tmp/*

# Install dlib
RUN mkdir -p /root/dlib
RUN git clone -b 'v19.24' --single-branch https://github.com/davisking/dlib.git /root/dlib/
RUN cd /root/dlib/ && \
    python3 setup.py install

# Install face_recognition
RUN cd ~ && \
    mkdir -p face_recognition && \
    git clone https://github.com/ageitgey/face_recognition.git face_recognition/ && \
    cd face_recognition/ && \
    pip3 install -r requirements.txt && \
    python3 setup.py install

# Install Django and project dependencies
COPY requirements.txt /app/requirements.txt
RUN pip3 install -r /app/requirements.txt

# Copy Django project
WORKDIR /app
COPY . .

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate && gunicorn face_recognition_attendance.wsgi:application --bind 0.0.0.0:8000"]
