from fastapi import FastAPI, Request, BackgroundTasks
from background_tasks.resume_insights import ResumeInsightsTask
from background_tasks.rag_queries import RagQueriesTask
from background_tasks.retrieval import FinalQuestionsTask
import os
from dotenv import load_dotenv
from os import getenv 
load_dotenv()
app= FastAPI()
strapi_auth_token = f"bearer {os.getenv('STRAPI_API_TOKEN')}"

def get_resume_insights_task(data):
    docid = data.get('documentId')
    task = ResumeInsightsTask()
    task.docid = docid
    task.run()
    
def get_rag_queries_task(data):
    docid = data.get('documentId')
    task = RagQueriesTask()
    task.docid = docid
    task.run()

def get_final_questions_task(data):
    docid = data.get('documentId')
    task = FinalQuestionsTask()
    task.docid = docid
    task.run()
       

@app.post("/hook")
async def hook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    print(data)
    background_tasks.add_task(get_resume_insights_task, data)
    background_tasks.add_task(get_rag_queries_task, data)
    background_tasks.add_task(get_final_questions_task, data)
    return {"message": "Hello World"}
