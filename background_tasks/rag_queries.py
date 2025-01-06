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

load_dotenv()


strapi_auth_token = f"bearer {os.getenv('STRAPI_API_TOKEN')}"
STRAPI_BASE_URL = getenv("STRAPI_API_URL")




class RagQuery(BaseModel):
    Query: str
    Description: str


class RagQueries(BaseModel):
    Queries: List[RagQuery]

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
structured_client = instructor.from_openai(OpenAI())
client = OpenAI()

class RagQueriesTask(luigi.Task):
    docid = luigi.Parameter("docid")
    #context = luigi.Parameter("context")


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

    def get_resume_insights_info(self,docid):
        url = f"{STRAPI_BASE_URL}/resume-insights"
        params = f"filter[applicant_detail][$eq]={docid}"
        headers = {
            "Authorization": strapi_auth_token
        }
        logger.info(f"Fetching resume insights data for docid {docid}")
        try:
            r = requests.get(url, params=params, headers=headers)
            self.resume_insights= r.json()['data'] 
            logger.info(f"Fetched resume insights data: {self.resume_insights}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch resume insights data for docid {docid}: {e}")  
            raise

    def generate_rag_queries(self):
        """
        This tool takes the resume text and role data and generates rag queries from it."
        """
        logger.info(" Generating rag queries from the resume and role data")
        try:
            self.rag_queries= structured_client.chat.completions.create(
                model = "gpt-4-turbo",
                response_model=RagQueries,
                messages= [
                    {
                        "role":"system",
                        "content": "You are an advanced assistant designed to generate concise and targeted retrieval queries that enable effective information gathering from vector databases and web searches. Using the candidate's resume insights, target company, target role, and role description, your task is to create queries that comprehensively address all aspects required for retrieval. These queries will later be used to frame interview questions but must not directly resemble questions themselves."
                        "Key Objectives:"
                        "Insight-Driven Queries: Leverage resume insights, including inferred strengths, weaknesses, and skill gaps, to craft queries that extract nuanced and actionable information. These should align with the candidate's suitability for the role and address potential challenges."
                        "Role and Skill Relevance: Focus queries on technical, domain-specific, and professional skills required for the role, ensuring alignment with the role description and expectations."
                        "Company-Specific Tailoring: Include queries that gather information about the target company’s culture, recent projects, industry challenges, and values to align the retrieval process with organizational expectations."
                        "Behavioral and HR Aspects: Incorporate queries that extract content related to situational judgment, leadership, teamwork, and communication skills, enabling preparation for behavioral and HR interviews."
                        "Query Requirements: Queries must be concise, specific, and tailored to the provided inputs. Focus on actionable information that supports retrieval rather than generating questions directly."
                        "Cover technical challenges, industry trends, skill-specific problems, and HR-related scenarios in a natural and fluid manner without rigid categorization."
                        "Example Guidelines:"
                        "For strengths: Highlight areas of excellence in alignment with the role."
                        "E.g., “Advanced statistical modeling techniques used in advertising analytics.”"
                        "For weaknesses: Identify areas where improvement or deeper exploration is beneficial."
                        "E.g., “Common challenges in applying machine learning for user behavior prediction.”"
                        "For the role: Focus on practical and theoretical challenges related to the target role."
                        "E.g., “Time-series forecasting methods for large-scale systems.”"
                        "For the company: Incorporate organization-specific challenges and values."
                        "E.g., “Google’s approach to scalable machine learning systems.”"
                        "For behavioral aspects: Extract insights into leadership, communication, and adaptability."
                        "E.g., “Effective leadership in cross-functional data science teams.”"
                        "We can also have queries that go like interview questions on RNNs,i mean that begin wiht interview questions for so-and-so topic but strike a balance on all the types and nuaces of queries. Generate only 10 queries."

                    },
                    {
                        "role":"user",
                        "content":f"The resume insights are {self.resume_insights}. The target role is {self.role_info.get('role')}. The target company is {self.role_info.get('company')}. The target role description is {self.role_info.get('description')}"
                    }

                ]
            )
            logger.info("Generated rag queries successfully")
            logger.debug(f"Rag queries: {self.rag_queries}")
        except Exception as e:
            logger.error(f"Failed to generate rag queries: {e}")
            raise


    def post_to_rag_queries_api_tool(self):
        """
        This tool takes the rag queries generated by the LLM and posts it to the Rag Queries API
        """
        logger.info("Posting rag queries to the API")
        try:
            for single in self.rag_queries.Queries:
                url = f"{STRAPI_BASE_URL}/queries"
                headers = {
                    "Authorization": strapi_auth_token
                }
                data = {
                    "data": {
                        "Queries": single.Query,
                        "Description": single.Description,
                        "applicant_detail": self.docid
                    }
                }
                r = requests.post(url, headers=headers, json=data)
                r.raise_for_status()
                
                time.sleep(1)
                logger.info(f"Posted rag queries to the API successfully")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to post rag queries to the API: {e}")
            raise    
    
    def run(self):
        self.get_role_data_by_id(self.docid)
        self.get_resume_insights_info(self.docid)
        self.generate_rag_queries()
        self.post_to_rag_queries_api_tool()

