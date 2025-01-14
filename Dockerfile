FROM python:3.8-slim
WORKDIR /app
COPY . /app/
RUN apt-get update && apt-get install mediainfo -y
RUN pip install -r requirements.txt
EXPOSE 8080
CMD ["python", "bot.py"]
