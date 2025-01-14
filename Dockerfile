FROM python:3.8-slim
WORKDIR /app
COPY . /app/
RUN sudo apt-get update && sudo apt-get install mediainfo
RUN pip install -r requirements.txt
EXPOSE 8080
CMD ["python", "bot.py"]
