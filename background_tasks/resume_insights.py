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




class ResumeInsight(BaseModel):
    insight: str

class ResumeInsights(BaseModel):
    insights: List[ResumeInsight]

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
structured_client = instructor.from_openai(OpenAI())
client = OpenAI()

class ResumeInsightsTask(luigi.Task):
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

    def get_resume_data_by_id(self, docid):
        """
        This tool takes an document id from the parameter 'docid' and returns the resume data. 
       
        """
        url = f"{STRAPI_BASE_URL}/applicant-details/{docid}?populate=*"
        headers = {
            "Authorization": strapi_auth_token
        }
        
        try:
            response = requests.get(url, headers=headers)
            data= response.json()
            print(data)
            base_url= "http://localhost:1337"
            resume_url= data['data']['Resume'][0]['url']
            self.final_url = base_url + resume_url
            print(f"Resume file URL: {response.json()}")
            print(self.final_url)
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch resume data for docid {docid}: {e}")
            raise

    def get_pdf_text(self):
        """
        This tool retrieves the PDF from the final URL and reads it into text.
        """
        logger.info(f"Fetching PDF from URL: {self.final_url}")
        try:
            response = requests.get(self.final_url)
            response.raise_for_status()  # Ensure we raise an error for bad responses
            with open("temp_resume.pdf", "wb") as f:
                f.write(response.content)

            
            reader = PdfReader("temp_resume.pdf")
            self.text = ""
            for page in reader.pages:
                self.text += page.extract_text() + "\n"
            logger.info("Successfully extracted text from PDF")
            time.sleep(3)
        except Exception as e:
            logger.error(f"Failed to fetch or read PDF: {e}")
            raise
 

    def generate_resume_insights(self):
        """
        This tool takes the resume text and generates insights from it."
        """
        logger.info(" Generating insights from the resume")
        try:
            self.resume_insights= structured_client.chat.completions.create(
                model = "gpt-4-turbo",
                response_model=ResumeInsights,
                messages= [
                    {
                        "role":"system",
                        "content": "You are an AI designed to analyze a candidate's resume and generate detailed insights to help craft tailored interview questions. Your goal is to balance factual extraction with meaningful inferences, focusing on insights that highlight the candidate's strengths, weaknesses, career trajectory, and potential alignment with a target role or company. Your output should consist of concise, actionable points to guide further processing." 
                                   "Key Insights to Extract and Infer:"
                                   "Core Strengths: Extract key skills, certifications, achievements, and domain expertise. Infer unique value propositions, transferable skills, and readiness for leadership or specialized roles."
                                   "Weaknesses/Gaps: Extract missing skills, career gaps, or short job tenures. Infer potential misalignments with the target role or industry, and identify areas needing development."
                                   "Career Trajectory: Extract role progression, transitions, and tenure lengths. Infer growth potential, risk appetite, and the ability to handle increasing responsibilities."
                                   "Behavioral and Cultural Fit: Extract indicators of teamwork, leadership, and value alignment. Infer preferred work environments, conflict resolution tendencies, and cultural alignment with the target company."
                                   "Role-Specific Insights: Extract relevant projects, tools, and methodologies. Infer immediate impact potential, domain readiness, and innovation tendencies."
                                   "Learning Agility: Extract recent certifications, courses, or upskilling efforts. Infer growth mindset, adaptability to industry trends, and readiness for new challenges."
                                   "Strategic and Soft Skills: Extract examples of communication, problem-solving, and leadership. Infer decision-making approaches, resilience, and capacity for strategic thinking. Generate only 10 insights."

                    },
                    {
                        "role":"user",
                        "content":f"The resume text is {self.text}. The target role is {self.role_info.get('role')}. The target company is {self.role_info.get('company')}. The target role description is {self.role_info.get('description')}"
                    }

                ]
            )
            logger.info("Generated resume insights successfully")
            logger.debug(f"Resume insights: {self.resume_insights}")
            time.sleep(3)
        except Exception as e:
            logger.error(f"Failed to generate resume insights: {e}")
            raise


    def post_to_resume_insights_api_tool(self):
        """
        This tool takes the resume insights generated by the LLM and posts it to the Resume Insights API
        """
        logger.info("Posting resume insights to the API")
        try:
            for single in self.resume_insights.insights:
                url = f"{STRAPI_BASE_URL}/resume-insights"
                headers = {
                    "Authorization": strapi_auth_token
                }
                data = {
                    "data": {
                        "insight": single.insight,
                        "applicant_detail": self.docid
                    }
                }
                r = requests.post(url, headers=headers, json=data)
                r.raise_for_status()
                
                time.sleep(3)
                logger.info(f"Posted resume insights to the API successfully")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to post resume insights to the API: {e}")
            raise    


   

   

       

    def run(self):
        logger.info("Starting ResumeInsightsTask run method")
        self.get_role_data_by_id(self.docid)
        self.get_resume_data_by_id(self.docid)
        self.get_pdf_text()
        self.generate_resume_insights()
        self.post_to_resume_insights_api_tool()

        logger.info("Finished ResumeInsightsTask run method")

