import luigi
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List
import requests
import os
from os import getenv
import instructor
from openai import OpenAI
from loguru import logger
from phi.agent import Agent
from phi.model.openai import OpenAIChat
from phi.tools.duckduckgo import DuckDuckGo
import json
from tenacity import retry, stop_after_attempt, wait_fixed

load_dotenv()

# --- ENVIRONMENT ---
strapi_auth_token = f"bearer {os.getenv('STRAPI_API_TOKEN')}"
STRAPI_BASE_URL = getenv("STRAPI_API_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

structured_client = instructor.from_openai(OpenAI())
client = OpenAI()

# --- Pydantic Models ---
class FinalQuestion(BaseModel):
    Question: str

class FinalQuestions(BaseModel):
    Questions: List[FinalQuestion]

# --- Base API Helper ---
class StrapiAPI:
    headers = {"Authorization": strapi_auth_token}

    @staticmethod
    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def fetch_queries(docid):
        url = f"{STRAPI_BASE_URL}/queries"
        params = {"filter[applicant_detail][$eq]": docid}
        r = requests.get(url, params=params, headers=StrapiAPI.headers)
        r.raise_for_status()
        return [item["Queries"] for item in r.json()["data"]]

    @staticmethod
    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def fetch_role_data(docid):
        url = f"{STRAPI_BASE_URL}/applicant-details/{docid}"
        r = requests.get(url, headers=StrapiAPI.headers)
        r.raise_for_status()
        data = r.json()["data"]
        return {
            "company": data.get("Target_Company"),
            "role": data.get("Target_Role"),
            "description": data.get("Role_Description"),
        }

# --- Agent ---
agent = Agent(
    model=OpenAIChat(id="gpt-4o"),
    tools=[DuckDuckGo()],
    description="You are a senior educational researcher",
    instructions=[
        "For each query, search top 3 links and extract article text.",
        "Summarize in one clean, standalone paragraph."
    ],
    markdown=True,
    show_tool_calls=False,
)

# --- Luigi Tasks ---
class FetchQueriesTask(luigi.Task):
    docid = luigi.Parameter()

    def output(self):
        return luigi.LocalTarget(f"data/queries_{self.docid}.json")

    def run(self):
        queries = StrapiAPI.fetch_queries(self.docid)
        with self.output().open("w") as f:
            json.dump(queries, f)


class FetchRoleTask(luigi.Task):
    docid = luigi.Parameter()

    def output(self):
        return luigi.LocalTarget(f"data/role_{self.docid}.json")

    def run(self):
        role_data = StrapiAPI.fetch_role_data(self.docid)
        with self.output().open("w") as f:
            json.dump(role_data, f)


class GenerateParagraphsTask(luigi.Task):
    docid = luigi.Parameter()

    def requires(self):
        return FetchQueriesTask(self.docid)

    def output(self):
        return luigi.LocalTarget(f"data/paragraphs_{self.docid}.json")

    def run(self):
        with self.input().open("r") as f:
            queries = json.load(f)

        paragraphs = []
        for query in queries:
            logger.info(f"Generating paragraph for query: {query}")
            try:
                response = agent.run(query)
                # Extract assistant content instead of only tool bodies
                text_blocks = [
                    m.content for m in response.messages if m.role == "assistant"
                ]
                combined = " ".join(text_blocks).strip()
                paragraphs.append(combined)
            except Exception as e:
                logger.error(f"Error generating paragraph: {e}")

        with self.output().open("w") as f:
            json.dump(paragraphs, f)


class GenerateQuestionsTask(luigi.Task):
    docid = luigi.Parameter()

    def requires(self):
        return {
            "paragraphs": GenerateParagraphsTask(self.docid),
            "role": FetchRoleTask(self.docid),
        }

    def output(self):
        return luigi.LocalTarget(f"data/questions_{self.docid}.json")

    def run(self):
        with self.input()["paragraphs"].open("r") as f:
            paragraphs = json.load(f)
        with self.input()["role"].open("r") as f:
            role_info = json.load(f)

        all_questions = FinalQuestions(Questions=[])

        for paragraph in paragraphs:
            logger.info(f"Generating questions for: {paragraph[:100]}...")
            try:
                response = structured_client.chat.completions.create(
                    model="gpt-4o-mini",
                    response_model=FinalQuestions,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are an AI interviewer creating structured, "
                                "domain-relevant interview questions. Follow instructions."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Role: {role_info['role']} at {role_info['company']}\n\n"
                                f"Context:\n{paragraph}"
                            ),
                        },
                    ],
                )
                all_questions.Questions.extend(response.Questions)
            except Exception as e:
                logger.error(f"Error generating questions: {e}")

        with self.output().open("w") as f:
            json.dump(all_questions.dict(), f, indent=2)


class FinalQuestionsPipeline(luigi.WrapperTask):
    docid = luigi.Parameter()

    def requires(self):
        return GenerateQuestionsTask(self.docid)









