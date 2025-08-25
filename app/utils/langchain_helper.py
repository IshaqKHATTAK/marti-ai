from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableParallel
from operator import itemgetter
from sqlalchemy.exc import SQLAlchemyError
import backoff
import logging
import math
from app.schemas.response.user_chat import Chats, GetMessagesResponse, GetMessagesResponseInternal
import openai
import requests
import json
from sqlalchemy import func
from fastapi import HTTPException,status
from langchain_core.messages import AIMessage, HumanMessage
from app.utils.database_helper import update_thread_prompt_and_counter, get_thread_data
from langchain_core.messages import ToolMessage
from app.models.chatbot_model import Threads, Messages, ChatbotDocument, BotType
from sqlalchemy import select, asc, desc
from sqlalchemy.orm import Session
from langchain.tools import Tool
from sqlalchemy.future import select
from langchain.vectorstores import Pinecone
from langchain.embeddings import OpenAIEmbeddings
from typing import List, Dict
from sqlalchemy import select
import openai
from typing import Optional
from app.models.user import User
from app.models.organization import Organization
from pinecone import Pinecone as PineconeClient
from app.common.env_config import get_envs_setting
from langchain_core.runnables import RunnableParallel, RunnableLambda, RunnableConfig
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from pydantic import BaseModel, Field, ConfigDict
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from typing import Annotated
from langchain_core.output_parsers import JsonOutputParser
from langchain.output_parsers import PydanticOutputParser
from typing import Optional
from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from app.utils.prompts import Intent_classifer_agent_prompt,simplify_agent_prompt_with_image, OUTPUT_FORMAT_INSTRUCTION_FOR_NO_IMAGE_GEN_SIMPLIFY,OUTPUT_FORMAT_INSTRUCTION_FOR_IMAGE_GEN_SIMPLIFY, simplify_agent_prompt_without_image,prompt_generator_prompt, general_sub_prompt_with_image_gen, general_sub_prompt_without_image_gen
from typing import Literal
from langgraph.checkpoint.postgres import PostgresSaver
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import AnyMessage, add_messages
from typing import Annotated, List
from typing_extensions import TypedDict
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver   #  <-- async saver
from psycopg import AsyncConnection    
from psycopg import Connection       
# from langchain_postgres.checkpoint import PostgresSaver     


envs = get_envs_setting()
from langchain.globals import set_verbose
set_verbose(True)
logger = logging.getLogger(__name__)

embeddings = OpenAIEmbeddings(api_key=envs.OPENAI_API_KEY, model=envs.EMBEDDINGS_MODEL)
pc = PineconeClient(api_key=envs.PINECONE_API_KEY)
index = pc.Index(envs.PINECONE_KNOWLEDGE_BASE_INDEX)

 
def _simple_prompt_assistant(llm, system_message: str):
  prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                system_message,
            ),
            MessagesPlaceholder(variable_name="user_input"),
        ]
    )
  return prompt | llm

def _simple_prompt_assistant_langgraph(llm, system_message: str):
  prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                system_message,
            ),
            MessagesPlaceholder(variable_name="messages"),
            MessagesPlaceholder(variable_name="user_input"),
            MessagesPlaceholder(variable_name="prompt_to_be_rewrite")
        ]
    )
  return prompt | llm


def load_llm(api_key, name, temperature = 0.2):
    '''Load model and return'''
    return ChatOpenAI(model = name, api_key = api_key, temperature = temperature)

def load_llm_in_json_mode(api_key, name, temperature = 0.2):
    '''Load model and return'''
    return ChatOpenAI(model = name, api_key = api_key, temperature = temperature, model_kwargs={ "response_format": { "type": "json_object" } })

async def format_user_question(user_input, images_urls):
    question = {
                "role": "human",
                "content": [
                    {
                        "type": "text",
                        "text": user_input
                    }
                ]
            }
    if images_urls:
        for image_url in images_urls:
            image_url_formatted = {
                "type": "image_url",
                "image_url": {
                    "url": image_url, 
                }
            }
            question["content"].append(image_url_formatted)

    return question
# SETUP KNOWLEDGE BASE CHAIN
######################################################    Langraph prompter graph     ###################################

async def intent_clafier_node(state):
      print(f'graph started')
      
      intent_classfier_assistant = _simple_prompt_assistant(llm = llm_for_langgraph, system_message= Intent_classifer_agent_prompt)
      # The messages variable in the state is a list of BaseMessage objects.
      # The last message is state["messages"][-1].
      # The history is the entire list of messages.
      response = await intent_classfier_assistant.ainvoke({"user_input": [{"role": "user", "content": state["messages"][-1].content}],
                                                            "messages": state["messages"]
                                                           }
                                                           )
      
      print(f'response from classifer == {response.content}')
      # if response.simlify.lower() == 'yes':
      #   state['next_node'] = 'simlify'
      # else:
      #   state['next_node'] = 'prompter'
      current_count = state["count"] if "count" in state else 0
    
      return {"simlify":json.loads(response.content)["simlify"].lower(),"count":current_count+1}

def intent_classifier_router(state) -> Literal["prompter_node", "__end__"]:
    messages = state['messages']
    last_message = messages[-1]

    # Here if intent classifer clasify to be a clasify simplify flow then route to simply or if decide to move to socaratic flow then move to socaratic flow.
    if state["simlify"].lower() == 'yes':
      state['next_node'] = ''
      return '__end__'
    elif state["simlify"].lower() == 'no':
      state['next_node'] = 'prompter_node'
      return 'prompter_node'
    # Otherwise, we stop (send state to outliner)
    state['next_node'] = ''
    return '__end__'


async def prompter_node(state):
      print(f'inside prompter  == {state["count"] % 5}')
      #state["count"] == 1 means on very first iteration ther should be some prompt generation.
      if (state["count"] != 1) and (state["count"] % 5) != 0:
          return {'next_node':"__end__"}
      prompter = _simple_prompt_assistant_langgraph(llm = llm_for_langgraph, system_message= prompt_generator_prompt)
      print(f'prompter invoke')
      promt_of_agent = state.get("prompt") or ""
      response = await prompter.ainvoke({"user_input": [{"role": "user", "content": state["messages"][-1].content}],
                                          "messages": state["messages"],
                                          "prompt_to_be_rewrite":[HumanMessage(content=promt_of_agent)]
                                        })
      #
      print(f'response prompter node == {response.content}')
    #   FINAL_PROMPT = response.content + "\n" + GENERAL_RETRIVAL_TOOL.strip()

      return {'prompt': response.content}


    

class StudentTeacherState(TypedDict):
        messages: Annotated[list, add_messages]
        next_node: str
        prompt: Optional[str]
        simlify: Optional[str]
        count: Optional[int]
        

llm_for_langgraph = load_llm(api_key=envs.OPENAI_API_KEY,name='gpt-4o-mini')
teacher_student_workflow = StateGraph(StudentTeacherState)
teacher_student_workflow.add_node("intent_classifier", intent_clafier_node)
teacher_student_workflow.add_node("prompter_node", prompter_node)
teacher_student_workflow.set_entry_point("intent_classifier")

teacher_student_workflow.add_conditional_edges(
    "intent_classifier",
    intent_classifier_router
)
teacher_Student_graph = teacher_student_workflow.compile()#checkpointer=checkpointer



async def socaratic_agent_prompt_generation_flow(message_history,already_exiting_prompt, enable_image_generation,user_question,thread_id,counter):
    print(f'graph started')
    # connection_kwargs = {
    #     "autocommit": True,
    #     "prepare_threshold": 0,
    # }
    # password = "Bytebricks&123"                # <- the piece that contains special chars
    # from urllib.parse import quote_plus
    # encoded_pw = quote_plus(password)

    # libpq_url = (
    #         f"postgresql://root:{encoded_pw}@postgres_chat:5432/fastapi_chat"
    #     )
    # from urllib.parse import quote_plus
    # encoded_pw = quote_plus("Bytebricks&123")
    # libpq_url = f"postgresql://root:{encoded_pw}@postgres_chat:5432/fastapi_chat"

    # 2. open an async connection
    # conn = await AsyncConnection.connect(
    #     libpq_url,
    #     autocommit=True,
    #     prepare_threshold=0,
    # )
    # 1. open *sync* connection
    # conn = Connection.connect(
    #     libpq_url,
    #     autocommit=True,
    #     prepare_threshold=0,
    # )

    # 2. create the check-pointer (sync)
    # checkpointer = PostgresSaver(conn)
    # checkpointer.setup()   
    # checkpointer = PostgresSaver(conn)
    # await checkpointer.setup()
    print('initilized')
    # memory = MemorySaver()
    

    config = {"configurable": {"thread_id": f"{thread_id}"}}
    results = await teacher_Student_graph.ainvoke(
        {"messages": message_history + [("user", user_question)],
            "prompt": already_exiting_prompt,
            "count": counter}, 
        config
        )
    print(f'excuted')
    print(f'state of graph is {results}')
    if results["simlify"].lower() == "no":
        if "prompt" in results:
            print(f'inside prompt')
            if enable_image_generation:
                print(f'inside image generation prompt')
                return results["prompt"] + "\n" + general_sub_prompt_with_image_gen.strip(),results["prompt"], results["count"]
            print(f'inside prompt out of imag gen')
            return results["prompt"] + "\n" + general_sub_prompt_without_image_gen.strip(), results["prompt"], results["count"]
        else:
            print(f'no prompt modifcation')
            return already_exiting_prompt, already_exiting_prompt, results["count"]
    elif results["simlify"].lower() == "yes":
        print(f'simplify ')
        if enable_image_generation:
            return simplify_agent_prompt_with_image + "\n" + OUTPUT_FORMAT_INSTRUCTION_FOR_IMAGE_GEN_SIMPLIFY,simplify_agent_prompt_with_image, 4 #when simlify prompt gets generated I must allow the promt creation that why 5
        else:
            return simplify_agent_prompt_without_image + "\n" +  OUTPUT_FORMAT_INSTRUCTION_FOR_NO_IMAGE_GEN_SIMPLIFY,simplify_agent_prompt_without_image, 4
    # conn.close()
    
    return already_exiting_prompt, already_exiting_prompt, results["count"]



# async def test_graph(messages):
#     print('test called')
#     prompt_generated = await socaratic_agent_prompt_generation_flow(message_history=messages, already_exiting_prompt="You are nice assistant", enable_image_generation=False, user_question="How can you help me with c++", thread_id=2222)
#     print(f'Prompt generated is == {prompt_generated}')
#     return

class ModerationResponseFormat(BaseModel):
    response: str = Field(description="Response either ALLOWED or NOT_ALLOWED")
        
async def _create_moderation_chain(llm,  guardrails, user_input):
    """
    Creates a moderation chain using guardrails from database
    
    Args:
        llm: The language model to use
        chatbot_id: ID of the chatbot to get guardrails for
        db_session: Database session
    """
 # Default if no guardrails found
    try:
        MODERATION_PROMPT = f"""
        You are a content moderator. You must respond with ONLY 'NOT_ALLOWED' if the input mentions or asks about any of these forbidden topics:

        FORBIDDEN TOPICS:
        {guardrails}
        
        For all other topics, respond with 'ALLOWED'.
        Remember: Your response must be ONLY 'ALLOWED' or 'NOT_ALLOWED' - no other text.

        ###Output format:
        {{{{"response":"ALLOWED"}}}}
        {{{{"response":"NOT_ALLOWED"}}}}
        YOU MUST FOLLOW THE EXACT JSON FORMAT
        """
        
        moderation_prompt = ChatPromptTemplate.from_messages([
            ("system", MODERATION_PROMPT),
            ("human", "{input}")
        ])
        moderation_chain = moderation_prompt | llm.with_structured_output(ModerationResponseFormat, strict = True)
        moderation_result = await moderation_chain.ainvoke({"input": user_input})
        print(f'meration results == {moderation_result.response}')
        if moderation_result.response.strip() == "ALLOWED":
            return True,''
        else:
            return False, "I apologize, but I'm not permitted to discuss this topic. Please feel free to ask me something else that aligns with our usage policies."
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing request: {str(e)}"
        )    

    # return moderation_prompt | llm | StrOutputParser()

def format_docs(docs):
    """Format documents into a single string with clear separation"""
    formatted_texts = []
    for match in docs:
        text = match["document"]["text"]
        # text = match['metadata'].get('text') or match['metadata'].get('summary', '')
        if text:
            formatted_texts.append(text)
    # for row in formatted_texts:
    #     print(f'finally passed data in row == {row}')
    return "\n\n".join(formatted_texts)

async def get_relevant_context(query, text_query, index, chatbot_id, org_id, top_n):
    # Get query embedding and search
    formatted_texts = []
    image_links = []

    # query_embedding = embeddings.embed_query(query)
    raw_results = index.query(
        vector=query,
        namespace=f'{org_id}-kb',
        top_k=25,
        include_metadata=True,
        filter={
            "content_source": "doc",
            "chatbot_id":chatbot_id
        }
    )

    filtered_results_for_ranker = [
        doc for doc in raw_results["matches"]
    ]
    return filtered_results_for_ranker

    print(f'data to pass to ranker in doc retrial == {filtered_results_for_ranker}')
    # Filter results where metadata["chatbot_id"] matches the given chatbot_id
    if not filtered_results_for_ranker:
        return {
        "text":"",
        "images":""
        }
    reranked_results = await retrvied_docs_re_ranker_for_doc_and_formater(filtered_results_for_ranker, text_query, top_n)
    ranked_ids = []
    for ranked_doc in reranked_results:
        ranked_ids.append(ranked_doc["document"]["id"])
    filtered_results = [
        doc for doc in raw_results["matches"] if doc.id in ranked_ids
    ]

    for match in filtered_results:
        metadata = match.get('metadata', {})
        if metadata.get("content_type") == "image":
            print(f'image detected and added {metadata["file_path"]}')
            image_links.append(metadata["file_path"])
        else:
            print(f'No image detected == {metadata["chunk_num"]}')
            text = metadata.get('text') or metadata.get('summary', '')
            if text:
                formatted_texts.append(text)
    print(f"data in doc retrieval == {formatted_texts}")
    print(f"data images url in doc == {image_links}")
    return {
        "text": "\n\n".join(formatted_texts) if formatted_texts else "No relevant text. Image URLs are provided below.",
        "images": image_links if image_links else "No relevant image"
    }
    
async def get_relevant_scrapped_context(query, embeddings, index, chatbot_id, org_id):
    # Get query embedding and search
    # query_embedding = embeddings.embed_query(query)
    raw_results = index.query(
        vector=query,
        namespace=f'{org_id}-kb',
        top_k=25,
        include_metadata=True,
        filter={
            "content_source": "url",
            "chatbot_id":chatbot_id
        }
    )
    # print(f"name space == {f'{org_id}-kb'} and chatbot_id == {chatbot_id} and content_source == url and feched results count == {len(raw_results['matches'])}")
    # print(f'reuslts raw_results["matches"] == {raw_results["matches"]}')
    filtered_results = [
        doc for doc in raw_results["matches"]
    ]
    print(f"data in scraped retrieval == {filtered_results}")
    return filtered_results

async def get_relevant_prompt_context(query, embeddings, index, chatbot_id, org_id):
    # Get query embedding and search
    # query_embedding = embeddings.embed_query(query)
    raw_results = index.query(
        vector=query,
        namespace=f'{org_id}-kb',
        top_k=25,
        include_metadata=True,
        filter={
            "content_source": "prompt",
            "chatbot_id":chatbot_id
        }
    )
    filtered_results = [
        doc for doc in raw_results["matches"]
    ]
    # print(f"data in scrapped prompt == {filtered_results}")
    return filtered_results

async def get_relevant_qa_context(query, embeddings, index, chatbot_id, org_id):
    # Get query embedding and search
    # query_embedding = embeddings.embed_query(query)
    raw_results = index.query(
        vector=query,
        namespace=f'{org_id}-kb',
        top_k=25,
        include_metadata=True,
        filter={
            "content_source": "qa_pair",
            "chatbot_id":chatbot_id
        }
    )
    
    filtered_results = [
        {
        "id":doc["id"],
        "text":doc["metadata"]["text"]
         } for doc in raw_results["matches"]
    ]
    return filtered_results

async def get_relevant_memory_context(query, embeddings, index, chatbot_id, org_id, memory_status):
    # Get query embedding and search
    # query_embedding = embeddings.embed_query(query)
    if not memory_status:
        return []
    raw_results = index.query(
        vector=query,
        namespace=f'{org_id}-kb',
        top_k=25,
        include_metadata=True,
        filter={
            "content_source": "memory",
            "chatbot_id":chatbot_id
        }
    )
    filtered_results = [
        {
        "id":doc["id"],
        "text":doc["metadata"]["text"]
         } for doc in raw_results["matches"]
    ]
    print(f"data in memory retrieval == {filtered_results}")
    return filtered_results
 
#Create the complete chain with moderation

# async def moderated_chain(moderation_chain, user_input) -> str:
#     try:
#         # Check moderation
#         moderation_result = await moderation_chain.ainvoke({"input": user_input})
        
#         if moderation_result.strip() != "ALLOWED":
#             return "I apologize, but I'm not permitted to discuss this topic. Please feel free to ask me something else that aligns with our usage policies."
        
#         # Process through main chain with config
#         return await main_chain.ainvoke(
#             {"input": input, "chat_history": []},
#             config=config
#         )
        
#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Error processing request: {str(e)}"
#         )

async def retrvied_docs_re_ranker_for_doc_and_formater(docs, text_query: str, top_n = 2):
    docs_result = docs["docs"]
    scrapped_result = docs["scrapped"] 
    prompt_result = docs["prompt"]

    filtered_results = [
        {
        "id":doc["id"],
        "text":doc["metadata"]["text"]
         } for doc in scrapped_result
    ]

    for doc in docs_result:
        filtered_results.append({
        "id":doc["id"],
        "text":doc["metadata"]["text"]
         })
    for doc in prompt_result:
        filtered_results.append({
        "id":doc["id"],
        "text":doc["metadata"]["text"]
         })
    print(f'documents being insertied to rerankder ----------------------------')
    print(f'documents ==== {filtered_results}')

    if not filtered_results:
        return f"texts: No relevant text content found. ,images: No relevant image content found."

    top_n_docs = pc.inference.rerank(
        model="pinecone-rerank-v0",
        query=text_query,
        documents=filtered_results,
        top_n=top_n,
        return_documents=True
    )
    print(f'added results =========')
    formatted_texts = []
    image_links = []
    ranked_ids = []
    image_ids = []

    for ranked_doc in top_n_docs.data:
        ranked_ids.append(ranked_doc["document"]["id"])
    filtered_results = [
        doc for doc in docs_result if doc.id in ranked_ids
    ]
    for match in filtered_results:
        metadata = match.get('metadata', {})
        if metadata.get("content_type") == "image":
            print(f'image detected and added {metadata["file_path"]}')
            image_links.append(metadata["file_path"])
            image_ids.append(match.id)
        else:
            print(f'No image detected == {metadata["chunk_num"]}')
            text = metadata.get('text') or metadata.get('summary', '')
            if text:
                formatted_texts.append(text)
    print(f"data in doc retrieval == {formatted_texts}")
    print(f"data images url in doc == {image_links}")
    
    
    # final_reulsts_of_doc_upload =  {
    #     "text": "\n\n".join(formatted_texts) if formatted_texts else "No relevant text. Image URLs are provided below.",
    #     "images": image_links if image_links else "No relevant image"
    # }

    #remove the image element
    top_n_docs.data = [
        ranked_doc for ranked_doc in top_n_docs.data 
        if ranked_doc["document"]["id"] not in image_ids
    ]

    formatted_texts = []
    for match in top_n_docs.data:
        text = match["document"]["text"]
        # text = match['metadata'].get('text') or match['metadata'].get('summary', '')
        if text:
            formatted_texts.append(text)
    for row in formatted_texts:
        print(f'finally passed data in row == {row}')
    
    text_content = "\n\n".join(formatted_texts) if formatted_texts else "No relevant text content found."
    image_content = "\n".join([f"Image: {img}" for img in image_links]) if image_links else "No relevant images found."

    # Alternative: More compact format
    combined_result = []

    if formatted_texts:
        combined_result.append(text_content)

    if image_links:
        combined_result.append(f"Referenced Images: {', '.join(image_links)}")

    if not formatted_texts and not image_links:
        combined_result.append("No relevant content or images found.")

    final_results_string = "\n\n".join(combined_result)
    return final_results_string
    
    # return top_n_docs.data

async def retrvied_docs_re_ranker(docs, text_query: str, top_n = 2):
    for dc in docs:
        print(f'dc top docs == {dc}')
    top_n_docs = pc.inference.rerank(
        model="pinecone-rerank-v0",
        query=text_query,
        documents=docs,
        top_n=top_n,
        return_documents=True
    )
    print(f'finished')
    # for row in top_n_docs.data:
    #     print(f'row -- {row}')
    return top_n_docs.data

def _create_custom_parser(schema_model):
    return JsonOutputParser(pydantic_object=schema_model) 



# async def construct_kb_chain(LLM_ROLE, LLM_PROMPT, llm, guardrails, chatbot_id: int, user_id: int, db_session):
async def construct_kb_chain(
        LLM_ROLE, 
        memory_status, 
        llm, chatbot_id: int, 
        org_id: int, 
        db_session, 
        enable_image_generation=False, 
        user_input = None, 
        top_n = 3, 
        chatbot_type = None, 
        chat_history = None, 
        thread_id = None):
    """
    Construct knowledge base chain with organization-specific namespace
    """
    try:
        # Initialize components
        print(f'envs.PINECONE_KNOWLEDGE_BASE_INDEX == {envs.PINECONE_KNOWLEDGE_BASE_INDEX}')
        # Setup chain components with namespace
        config = RunnableConfig(
            tags=["kb-retrieval"],
            metadata={"namespace": f'{org_id}-kb'},
            recursion_limit=25,
            max_concurrency=5
        )
        
        image_instruction = (
            "- **If the user asks for an image, you MUST call the `generate_image` tool instead of responding with text.**\n"
            "- If unsure, assume the user wants an image if they mention words like 'create', 'draw', or 'generate'.\n"
            "YOU MUST CALL **generate_image** TOOL WHEN IMAGE NEED TO BE GENERATED.\n"
        ) if enable_image_generation else ""

        # Define structured output format using Pydantic
        class StructuredFormat(BaseModel):
            answer: str = Field(description="Response to user question/input")
            image_url: Optional[str] = Field(description="Generated image URL if it exists, otherwise null")
        query_embedding = embeddings.embed_query(user_input)
        # parser = PydanticOutputParser(pydantic_object=StructuredFormat)
        building_stories_parser = _create_custom_parser(schema_model=StructuredFormat)
        prompt = ChatPromptTemplate.from_messages([
            ("system", f"""You are a {LLM_ROLE} assistant. Your primary objective is to generate precise, contextually relevant, and well-structured responses. 
            Prioritize the provided data sources, stick to the exact provided content, prioritize the following references, ensuring that key details are incorporated before relying on general knowledge. 

            **Core Behavior:**
            - You must rely strictly on the provided data sources to answer user queries.
            - If there is no data available from the File Extracted Data:
                - Ask politely the user to upload data to the knowledge base so you can assist him with your question.
                - HOWEVER, you may still respond to basic greetings such as "Hi", "Hello", "Hey", etc., in a friendly and human-like manner.
            - DO NOT attempt to answer complex or knowledge-based queries without context from the provided data.

            **Key Instructions:**
            - Respond in the same language as the user's input. Default to English if uncertain.
                {image_instruction}
                - You MUST describe only the provided images.   
                - DO NOT generate a random image description.    
                
            **Contextual References:**
            - **File Extracted Data:** {{context}} 
            - **Example Response Format Reference :** {{qa_context}}
                    - This provides response format and style guidelines.  
                    - Use it as a guiding framework for structuring and formatting responses.  
            
            Given the above references, generate a response that is factually accurate, contextually aligned, and well-structured and formatted.
            Clearly indicate when information is drawn from specific contexts to enhance transparency.
            
            \n**Output Format must follow the below json schema:**  
            {{{{'answer':'Response to user question/input'
                'image_url':'Generated image URL if it exists, otherwise null'
            }}}}
            """),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", """{input}""")
            ])
        print(f'chatbot type -- = {chatbot_type} -- {BotType.teacher}')
        if chatbot_type == BotType.teacher: 
            
            thread_data = await get_thread_data(db_session,thread_id)
            
            prompt_generated, prompt_part_to_be_added_to_db, counter = await socaratic_agent_prompt_generation_flow(message_history=chat_history, already_exiting_prompt=thread_data.agent_generated_prompt, enable_image_generation=enable_image_generation, user_question=user_input, thread_id=thread_id,counter=thread_data.questions_counter)
            prompt = ChatPromptTemplate.from_messages([("system",prompt_generated),
                                                    MessagesPlaceholder(variable_name="chat_history"),
                                                        ("human", """{input}""")])
            
            await update_thread_prompt_and_counter(db_session, prompt_part_to_be_added_to_db, counter,thread_id)
            
    
        async def async_format_docs(x):
            context_data = await get_relevant_context(query = query_embedding, text_query=user_input, index=index, chatbot_id=chatbot_id, org_id=org_id,top_n=top_n)
            return context_data

        async def async_format_scrapped(x):
            context_data = await get_relevant_scrapped_context(query = query_embedding, embeddings=embeddings,index=index, chatbot_id=chatbot_id, org_id=org_id)
            # if len(context_data) >= 1:
            #     retrvied_from_ranker = await retrvied_docs_re_ranker(context_data, user_input, top_n = top_n)
            #     return retrvied_from_ranker
            return context_data
        
        async def async_format_prompt(x):
            context_data = await get_relevant_prompt_context(query = query_embedding, embeddings=embeddings,index=index, chatbot_id=chatbot_id, org_id=org_id)
            # if len(context_data) >= 1:
            #     retrvied_from_ranker = await retrvied_docs_re_ranker(context_data, user_input,  top_n = top_n)
            #     return retrvied_from_ranker
            return context_data
        
        async def async_format_qa(x):
            context_data = await get_relevant_qa_context(query = query_embedding, embeddings=embeddings,index=index, chatbot_id=chatbot_id, org_id=org_id)
            if len(context_data) >= 1:
                retrvied_from_ranker = await retrvied_docs_re_ranker(context_data, user_input,  top_n = top_n)
                return format_docs(retrvied_from_ranker)
            return ""

        async def async_format_memory(x):
            context_data = await get_relevant_memory_context(query = query_embedding, embeddings=embeddings,index=index, chatbot_id=chatbot_id, org_id=org_id, memory_status = memory_status)
            if len(context_data) >= 1:
                retrvied_from_ranker = await retrvied_docs_re_ranker(context_data, user_input,  top_n = top_n)
                return format_docs(retrvied_from_ranker)
            return ""
        

        async def async_context_retrieval_and_rerank(chain_input):
            # Run the three context functions in parallel
            context_retrieval = RunnableParallel({
                "docs": RunnableLambda(async_format_docs),
                "scrapped": RunnableLambda(async_format_scrapped),
                "prompt": RunnableLambda(async_format_prompt),
            })
            # Get results from parallel execution
            context_results = await context_retrieval.ainvoke(chain_input)
            
            # Apply re-ranking to the combined results
            re_ranked_result = await retrvied_docs_re_ranker_for_doc_and_formater(
                context_results, user_input, top_n=4
            )
            print(f'*****************************************************************')
            print(f're_ranked_result == {re_ranked_result}')
            print(f"*****************************************************************")
            return re_ranked_result
        
      
        
        
        setup_and_retrieval = RunnableParallel({
            "Memory":RunnableLambda(async_format_memory),
            "context":RunnableLambda(async_context_retrieval_and_rerank),
            "input": itemgetter("input"),
            "chat_history": itemgetter("chat_history"),
            "qa_context": RunnableLambda(async_format_qa),
        })

        tools = []
        if enable_image_generation:
            tools.append(generate_image)

        if enable_image_generation:
            # Define function calling chain
            main_chain = setup_and_retrieval | prompt | llm.bind_tools(tools, strict = True)
            
        else:
            # Default text-based response chain
            main_chain = setup_and_retrieval | prompt | llm.with_structured_output(StructuredFormat, strict = True)
            
        # return moderated_chain
        return main_chain

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error while chatting"
        )


# This function should run when user gets created as per user there is only chat.
async def _create_thread_entery(thread_id,user_id ,db_session):
    try:    
      thread_entery = Threads(
            thread_id = str(thread_id),
            user_id = user_id,
            title = 'nothing',
            )
      db_session.add(thread_entery)
      await db_session.commit()
      return thread_entery
    except SQLAlchemyError as e:
      await db_session.rollback()  
      raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error occurred: {str(e)}"
        )
    except Exception as e:
      raise HTTPException(
          status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
          detail=f"An unexpected error occurred: {str(e)}"
      )


@backoff.on_exception(backoff.expo, Exception, max_time=90, jitter=backoff.random_jitter, logger=logger)
async def _add_message_database(thread_id, message_uuid,role, message, db_session, is_image = None, images_urls = None, organization_id = 0):
    try:   
        if is_image:
            message_data = Messages(
                thread_id = thread_id,
                organization_admin_id = organization_id,
                role = role,
                message_uuid = message_uuid,
                message_content = message,
                is_image = True,
                images_urls = images_urls)
            db_session.add(message_data)
        else:
            message_data = Messages(
                thread_id = thread_id,
                organization_admin_id = 1,
                message_uuid = message_uuid,
                role = role,
                message_content = message)
            db_session.add(message_data)

        db_session.add(message_data)
        await db_session.commit()
        return 
    except SQLAlchemyError as e:
        await db_session.rollback()  
        raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database error occurred: {str(e)}"
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(e)}"
        )

async def _create_langchain_history(thread_id, db_session):
    langchain_chat_history = []
    result = await db_session.execute(select(Messages).filter(Messages.thread_id == str(thread_id)).order_by(asc(Messages.created_timestamp)) )
    for message in result.scalars().all():
        if message.role == "User":
            langchain_chat_history.append(HumanMessage(content=message.content))
        else:
            langchain_chat_history.append(AIMessage(content=message.content))
        print(message.message_content)
    
    return langchain_chat_history

async def _load_last_10_messages(thread_id, db_session):
    langchain_chat_history = []
    result = await db_session.execute(select(Messages).filter(Messages.thread_id == thread_id).order_by(desc(Messages.created_timestamp)).limit(10) )
    last_10_messages = result.scalars().all()[::-1]
    for message in last_10_messages:
        if message.role == "User":
            print(f'human message == {message.message_content}')
            langchain_chat_history.append(HumanMessage(content=json.loads(message.message_content)))
            # if message.is_image:
            #     langchain_chat_history.append(HumanMessage(content=[ 
            #             {"type": "text", "text": message.message_content},
            #             {"type": "image_url", "image_url": {"url": message.images_urls}}
            #         ]))
            # else:
            #     langchain_chat_history.append(HumanMessage(content=message.message_content))
            
        elif message.role == "Assistant":
            langchain_chat_history.append(AIMessage(content=message.message_content))
        
            # if message.is_image:
            #     langchain_chat_history.append(AIMessage(content=[
            #         {"type": "text", "text": message.message_content},
            #         {"type": "image_url", "image_url": {"url": message.images_urls}}
            #     ]))
            # else:
        elif message.role == "Tool":
            if not message.is_image:
                print(f'tool message called')
                load_data = json.loads(message.message_content)
                additional_kwargs = load_data.get('additional_kwargs', {})  # Default to empty dict if missing
                response_metadata = load_data.get('response_metadata', {})  # Default to empty dict if missing
                tool_calls = load_data.get('tool_calls', [])  # Default to empty list if missing
                message_id = load_data.get('id', '') 
                
                langchain_chat_history.append(AIMessage(
                    content = '',
                    additional_kwargs = additional_kwargs, 
                    response_metadata = response_metadata, 
                    id = message_id, 
                    tool_calls = tool_calls))
            else:
                print(f'tool response called with ruls == {message.images_urls[0]}')
                langchain_chat_history.append(ToolMessage(content=message.message_content,  tool_call_id=message.images_urls[0]))
                
        
    return langchain_chat_history

async def _load_message_history(thread_id, db_session, skip: int, limit: int, internal = False, start_date=None, end_date=None):
    langchain_chat_history = []
    # Get total message count for pagination
    query  = select(func.count()).filter(Messages.thread_id == thread_id)
    if start_date:
        query  = query.filter(Messages.created_timestamp >= start_date)
    if end_date:
        query  = query.filter(Messages.created_timestamp <= end_date)
    total_messages_query = await db_session.execute(query)
    total_messages = total_messages_query.scalar()
    if skip > total_messages:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Messages not exist for this limit.')

    # total_messages_query = await db_session.execute(
    #     select(func.count()).filter(Messages.thread_id == thread_id)
    # )

    # total_messages = total_messages_query.scalar()
    # if skip > total_messages:
    #     raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Messages not exist for this limit.')
    
    print(f'skip == {skip} and limit ={limit}')
    if skip == 0 and limit == 0:
        result =  ( select(Messages)
            .filter(Messages.thread_id == thread_id)
            .order_by(desc(Messages.created_timestamp))  # Fetch latest messages first
            .offset(0) )
        #     .limit(20)
        # result = await db_session.execute(
        #     select(Messages)
        #     .filter(Messages.thread_id == thread_id)
        #     .order_by(desc(Messages.created_timestamp))  # Fetch latest messages first
        #     .offset(0)
        #     .limit(20)
        # )
    else:
        result =  (select(Messages)
            .filter(Messages.thread_id == thread_id)
            .order_by(asc(Messages.created_timestamp))
            .offset(skip) 
            .limit(limit))
        # result = await db_session.execute(
        #     select(Messages)
        #     .filter(Messages.thread_id == thread_id)
        #     .order_by(asc(Messages.created_timestamp))
        #     .offset(skip) 
        #     .limit(limit)
        # )
    if start_date:
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        print(f'starte data == {start_date}')
        result  = result.filter(Messages.created_timestamp >= start_date)
    if end_date:
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        print(f'starte data == {end_date}')
        result  = result.filter(Messages.created_timestamp <= end_date)
    
    query_result = await db_session.execute(result)

    load_messages = query_result.scalars().all()
    # if skip == 0 and limit == 0:
    #     load_messages.reverse()
    chat_messages = []
    image_generation = False
    for message in load_messages:
        print(f'user role == {message.role.value}')
        if message.role == 'User':
            user_input = json.loads(message.message_content)
            print(f'user get')
            chat_entry = Chats(
            role='human',
            message= user_input[0]["text"],
            images_urls=message.images_urls if message.images_urls else None,
            message_id = message.message_uuid,
            is_revise = message.is_revised
            )
            chat_messages.append(chat_entry)
        elif message.role == 'Assistant':
            print(f'asssitant messsage')
            chat_entry = Chats(
                role='ai',
                message= message.message_content,
                images_urls=message.images_urls if message.images_urls else None,
                message_id = message.message_uuid,
                is_revise = message.is_revised
            )
            chat_messages.append(chat_entry)
        else:
            # data = json.loads(message.message_content)
            print(f'tool data')

    if load_messages: 
        last_message = load_messages[-1] 
        if last_message.role == 'Tool' or (last_message.role == "User" and last_message.is_image):  # Assuming 'Tool' is the role name
            image_generation = True
            special_message = Chats(
                role='ai',
                message="",
                images_urls=None,
                message_id=last_message.message_uuid,
                is_revise = False
            )
            chat_messages.append(special_message)
    if internal:
        return GetMessagesResponseInternal(
             id = thread_id,
            image_generation = image_generation,
            chat_messages=chat_messages,
            offset = skip if skip else -1
        )
    return GetMessagesResponse(id=thread_id, chat_messages=chat_messages, image_generation = image_generation, total_messages=total_messages)


def _builtin_parser_assistant(llm: BaseChatModel, system_message: str):
    """
    A LangChain-based function that extracts user intent and generates an image description if needed.
    
    Parameters:
    - llm: The language model instance.
    - format: Output format (structured JSON).
    - system_message: System instruction for the assistant.
    
    Returns:
    - A chain that determines if an image is needed and generates an appropriate prompt.
    """ 
    system_message += '''\nOutput in JSON having the following schema:
    {{
    'intent':'yes/no'
    'image_description':'detailed description of image that user wants'
    }}'''
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_message),
            MessagesPlaceholder(variable_name="chat_history"), 
            MessagesPlaceholder(variable_name="user_input"),  
        ]
    )
    # prompt = ChatPromptTemplate.from_messages(
    #     [
    #         ("system", system_message),
    #         MessagesPlaceholder(variable_name="user_input"),  # Last messages in conversation
    #     ]
    # )

    # structured_format = {
    #     "intent": JsonOutputKey(description="Does the user want an image? (Yes/No)"),
    #     "image_description": JsonOutputKey(description="A detailed image generation prompt if intent is 'Yes', otherwise null")
    # }
    
    class structured_format(BaseModel):
        intent: str = Field(description="Does the user want an image? (Yes/No)")
        image_description: str = Field(description="A detailed image generation prompt if intent is 'Yes', otherwise null")
        
    return prompt | llm.with_structured_output(structured_format, method="json_mode")


async def intent_classifier(llm, chat_history, user_input):
    system_message = '''You are an intelligent assistant that detects if the user wants an image and generates a relevant image based on provided input/description.
    You should intelligently decide if the user wants an image based on the user input and the last two messages from the chat history.
    
    Chat History: {chat_history}'''
    
    formatted_sys_message = system_message.format(chat_history=chat_history)
    print(f'formated prompt == {formatted_sys_message}')
    # Create the assistant pipeline
    assistant_chain = _builtin_parser_assistant(llm, system_message=formatted_sys_message)

    # Ensure chat_history is properly formatted
    chat_history_messages = [{"role": "user", "content": msg} for msg in chat_history[-2:]]  # Last two messages

    results = await assistant_chain.ainvoke({
        "chat_history": chat_history_messages,  # Corrected: passing history correctly
        "user_input": [{"role": "user", "content": user_input}]
    })

    return results 
    # user_messages = [
    #     {"role": "user", "content": f"{last_message}"},
    #     {"role": "user", "content": f"{second_last_message}"}
    # ]
    # Run the assistant pipeline
    # result = assistant_chain.invoke({"user_input": user_messages})
    
    # print(f'result == {result}')
    # return result



@tool
def generate_image(image_prompt: Annotated[str, "description of an image user wants to generate"]):
    """Use this tool to generate an image."""
    try:
        print('generate imge calld')
        response =  openai.OpenAI().images.generate(
            model="dall-e-3", 
            prompt=image_prompt,
            n=1,
            size='1024x1024'
        )
        image_url = response.data[0].url
        return image_url
        # Create directory for storing images
        image_dir = "static/generated_images"
        import os
        import aiohttp
        import aiofiles
        
        os.makedirs(image_dir, exist_ok=True)

        filename = f"test.png"
        file_path = os.path.join(image_dir, filename)
        # Download image
        img_response = requests.get(image_url)
        if img_response.status_code == 200:
            with open(file_path, "wb") as f:
                f.write(img_response.content)
            
            print("Image generation completed!")
            # return file_path  # Return local file path
            return image_url
        else:
            print("Failed to download image.")
            return ""
        # async with aiohttp.ClientSession() as session:
        #     async with session.get(image_url) as resp:
        #         if resp.status == 200:
        #             async with aiofiles.open(file_path, "wb") as f:
        #                 await f.write(await resp.read())
        #             print('image generation done')
        #             return file_path  # Return local path
        #         else:
        #             return ''
        
    except Exception as e:
        print(f"Error generating image: {e}")
        return None
    


    