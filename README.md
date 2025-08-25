# Auto Generating Migrations (FastAPI + SQLAlchemy + Alembic)

### Installation & Configuration
- Install the Docker Desktop and Start It 
- Open the Terminal and navigate to the project folder.
- Run `docker volume create bytebricks_mysql_data` to create a docker volue in you machine. Required to persist the mysql data.
- Below will be mysql connection details
```bash
MYSQL_HOST=postgres_chat
MYSQL_USER=root
MYSQL_PASSWORD=Bytebricks&123
MYSQL_DB=fastapi_chat
MYSQL_PORT=3306
```
You do not need to change anything here, but if you would like to change the username, password or database name, you can modify it at this point in the `.env` file attached to this project. 

### Building the Project
- We can start building our projects by running `docker-compose build`
- One build is done, run `docker-compose up` to start the services. Leave this terminal open to check the logs.
- To stop the services you can press `Ctrl + C` - (Control + C)

### Commands
- To Generate the Migration From Model (DONT RUN THIS, IN FACT USE THE EXISITNG VERSION)
```
docker-compose run fastapi-service-chat /bin/sh -c "alembic revision --autogenerate -m "revised_table table""
```
- To Apply the Migration to Database 
```
docker-compose run fastapi-service-chat /bin/sh -c "alembic upgrade head"
```
- To Revert last applied migration
```
docker-compose run fastapi-service-chat /bin/sh -c "alembic downgrade -1"
```

# Accessing the Applications
- FastAPI Application Status [http://localhost:8001](http://localhost:8001)
- API Documentation [http://localhost:8001/docs](http://localhost:8001/docs)
- Database Access [http://localhost:8080](http://localhost:8080) - use the above detail to login.
- Mailpit [http://localhost:8025](http://localhost:8025)
# fastapi-sqlalchemy-alembic

# Pull docker images
- az login
- az acr login --name gdrivechatbot
- docker-compose pull

# Start/Stop locally
- docker-compose up -d
- docker-compose down

# Build and push docker image for Azure Contaier Registry (NEVER USE THIS)
- az login
- az acr login --name gdrivechatbot
- docker build -t gdrivechatbot.azurecr.io/fastapi:latest -f Dockerfile.cloud .
- docker push gdrivechatbot.azurecr.io/fastapi:latest