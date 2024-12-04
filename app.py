from fastapi import FastAPI, Request, BackgroundTasks
import os
app= FastAPI()
strapi_auth_token = f"bearer {os.getenv('STRAPI_API_TOKEN')}"
@app.post("/hook")
async def hook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    print(data)
    return {"message": "Hello World"}