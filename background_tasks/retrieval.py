import luigi
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import Optional, List
from enum import Enum
import requests
import os
from os import getenv
import instructor
from openai import OpenAI
import time
from loguru import logger
from PyPDF2 import PdfReader
from phi.agent import Agent
from phi.model.openai import OpenAIChat
from phi.tools.duckduckgo import DuckDuckGo
import json
load_dotenv()


strapi_auth_token = f"bearer {os.getenv('STRAPI_API_TOKEN')}"
STRAPI_BASE_URL = getenv("STRAPI_API_URL")






OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
structured_client = instructor.from_openai(OpenAI())
client = OpenAI()

class finalquestion(BaseModel):
    Question: str
    


class FinalQuestions(BaseModel):
    Questions: List[finalquestion]

class FinalQuestionsTask(luigi.Task):
    docid = luigi.Parameter("docid")
    #context = luigi.Parameter("context")


    def get_queries_info(self,docid):
        url = f"{STRAPI_BASE_URL}/queries"
        params = f"filter[applicant_detail][$eq]={docid}"
        headers = {
            "Authorization": strapi_auth_token
        }
        logger.info(f"Fetching queries data for docid {docid}")
        try:
            r = requests.get(url, params=params, headers=headers)
            self.queries= [item ['Queries'] for item in r.json()['data'] ]
            logger.info(f"Fetched queries data: {self.queries}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch queries data for docid {docid}: {e}")  
            raise

    def get_role_data_by_id(self, docid):
        """
        This tool takes an application id from the parameter 'appid' and returns the role data.
        It reads the role name and role description from the API
        """
        url = f"{STRAPI_BASE_URL}/applicant-details/{docid}"
        headers = {
            "Authorization": strapi_auth_token
        }
        logger.info(f"Fetching role data for docid {docid}")
        try:
            r = requests.get(url, headers=headers)
            r.raise_for_status()
            self.role_info = {
                "company": r.json().get("data").get('Target_Company'),
                "role": r.json().get("data").get("Target_Role"),
                "description": r.json().get("data").get("Role_Description")
            }
            logger.info(f"Fetched role data: {self.role_info}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch role data for docid {docid}: {e}")
            raise    

    agent = Agent(
        model=OpenAIChat(id="gpt-4o"),
        tools=[DuckDuckGo()],
        description="You are a senior educational researcher with a knack for obtaining required information and content based on a query",
        instructions=[
            "For a given query, search for the top 3 links on the web.",
            "Then read each URL and extract the article text, if a URL isn't available, ignore it.",
            "Analyse and prepare a comprehensive paragraph on that which can be used for further purposes.",
        ],
        markdown=True,
        show_tool_calls=False,
        add_datetime_to_instructions=False,
        # debug_mode=True,
    )

    def generate_paragraphs_from_queries(self):
        """
        This function takes each query from self.queries, uses the agent to generate a comprehensive paragraph,
        and stores all responses in self.paragraphs.
        """
        self.paragraphs = []  # Initialize the list to store paragraphs
        for query in self.queries:
            logger.info(f"Processing query: {query}")
            try:
                response = self.agent.run(query)  # Run the agent with the current query
                
                # Extract the body content from the tool responses
                bodies = []
                for message in response.messages:
                    if message.role == 'tool':
                        # Parse the JSON content to extract the body
                        tool_response = json.loads(message.content)
                        for item in tool_response:
                            if 'body' in item:
                                bodies.append(item['body'])  # Append the body content directly
                
                # Combine all bodies into a single paragraph
                combined_paragraph = " ".join(bodies)
                self.paragraphs.append(combined_paragraph)  # Store the combined response in self.paragraphs
                
                logger.info(f"Generated paragraph for query: {query}")
                logger.info(f"Generated paragraphs: {self.paragraphs}")
            except Exception as e:
                logger.error(f"Failed to generate paragraph for query '{query}': {e}")

   

    def generate_chat_completions(self):
        """
        This function takes each paragraph from self.paragraphs and uses the chat model to generate responses
        based on the content of each paragraph. The generated responses are stored in self.questions.
        """
        self.questions = FinalQuestions(Questions=[])  # Initialize the structured output for questions
        for paragraph in self.paragraphs:
            logger.info(f"Generating chat completion for paragraph: {paragraph}")
            try:
                response = structured_client.chat.completions.create(
                    model="gpt-4-turbo",
                    response_model=FinalQuestions,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are an AI designed to generate comprehensive, detailed, and to-the-point interview questions based on provided paragraphs of context. Your goal is to craft high-quality questions tailored to specific interview scenarios. The questions must be suitable for interviews conducted by major companies in the domain, relevant roles, and general skill/situational evaluations."
                                        "Follow these instructions closely:"
                                        "Types of Questions to Generate:"
                                        "Company-Based: Questions tailored to what major companies in the domain (e.g., Google, Microsoft, Meta) are likely to ask. These should focus on high-level challenges, enterprise use cases, or innovations in the field."
                                        "Role-Based: Questions targeting the relevant roles (e.g., Data Scientist, Data Analyst, ML Engineer) mentioned in the context. These should cover responsibilities, workflows, and problem-solving within those roles."
                                        "Skill-Based: Questions focusing on specific skills, situational problem-solving, or behavioral aspects. These include technical scenarios, debugging, practical skills, and HR-type questions."
                                        "Context Utilization:"
                                        "Thoroughly analyze the provided paragraph(s) of context to extract key themes, challenges, tools, or methodologies."
                                        "If the context supports only one or two types of questions (e.g., role-based and skill-based), focus on those types. Do not force unrelated questions."
                                        "Output Format:"
                                        "Provide a balanced set of technical, practical, and behavioral questions by the end of the document."
                                        "Clearly label each question with its type in parentheses: (company-based), (role-based), or (skill-based)."
                                        "Ensure the questions are detailed, specific, and relevant to real-world scenarios."
                                        "Question Guidelines:"
                                        "Questions must be precise and actionable, avoiding vagueness or overly broad phrasing."
                                        "Avoid repeating similar questions across types or contexts."
                                        "Keep the questions aligned with current industry practices and trends."
                                        "Examples of Question Types:"
                                        "Company-Based:"
                                        "How does a company like Google address latency issues in retrieval-augmented generation (RAG) systems at scale? (company-based)"
                                        "Role-Based:"
                                        "As a Data Scientist, how would you handle imbalanced datasets when building a classification model for fraud detection? (role-based)"
                                        "Skill-Based:"
                                        "Explain the steps you would take to debug a data pipeline built on PyTorch IterableDataset for distributed training. (skill-based)"
                                        "End Goal:"
                                        "Ensure the final set of questions includes a balanced mix of technical, behavioral, and practical assessments across the three categories."
                                        "Prioritize diversity and relevance to maximize the coverage of potential interview scenarios."
                                        "Behavior"
                                        "Generate a set of questions for each paragraph of context. If the context strongly supports certain types (e.g., technical skill-based questions), focus on those while maintaining the overall balance. If additional instructions or refinement is needed, await user feedback before proceeding.Generate 5 questions per paragraph."
                        },
                        {
                            "role": "user",
                            "content": f"The following is the context for the role of {self.role_info['role']} at {self.role_info['company']}: the paragraph context is\n\n{paragraph}"
                        }
                    ]
                )
                
                # Append the questions from the response to self.questions
                self.questions.Questions.extend(response.Questions)  # Assuming response.Questions is a list of finalquestion objects
                
                logger.info("Generated questions successfully")
                logger.info(self.questions)
            except Exception as e:
                logger.error(f"Failed to generate questions: {e}")
                raise
           
                
       

    def create_pdf(self, filename="output.pdf"):
        """
        This function creates a PDF file containing the structured questions generated from the paragraphs.
        """
        from fpdf import FPDF  # Ensure you have fpdf installed: pip install fpdf

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Arial", size=12)

        # Check if questions are generated
        if hasattr(self, 'questions') and self.questions:
            # Extract questions from the structured output
            for question in self.questions.Questions:
                # Replace unsupported characters
                safe_question = question.Question.encode('latin-1', 'replace').decode('latin-1')
                pdf.multi_cell(0, 10, f"Question: {safe_question}\n\n")

        else:
            pdf.multi_cell(0, 10, "No questions generated.\n\n")

        pdf.output(filename)
        logger.info(f"PDF created successfully: {filename}")

    def run(self):
        self.get_queries_info(self.docid)
        self.get_role_data_by_id(self.docid)
        self.generate_paragraphs_from_queries()
        self.generate_chat_completions()
        self.create_pdf()








