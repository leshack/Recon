# FROM python:3.10-slim

# WORKDIR /app

# RUN apt-get update && apt-get install -y git

# RUN git clone https://github.com/leshack/Recon.git .

# RUN pip3 install -r requirements.txt

# EXPOSE 7070

# ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=7070", "--server.address=localhost"]

FROM python:3.10-slim

WORKDIR /app

# Install git temporarily and clone the repository
RUN apt-get update && apt-get install -y git \
    && git clone https://github.com/leshack/Recon.git . \
    && rm -rf .git \
    && apt-get remove -y git \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port 7070
EXPOSE 7070

# Run Streamlit app and make it accessible externally
ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=7070", "--server.address=0.0.0.0"]
