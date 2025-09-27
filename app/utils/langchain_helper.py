from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser
# Removed unused imports for simplified routing
from sqlalchemy.exc import SQLAlchemyError
import backoff
import logging
from app.utils.simple_agent import stream_general_graph as stream_simple_agent_graph
from app.models.chatbot_model import ScaffoldingLevel
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
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from typing import Annotated
from langchain_core.output_parsers import JsonOutputParser
from typing import Optional
from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from app.utils.prompts import Intent_classifer_agent_prompt,simplify_agent_prompt_with_image, OUTPUT_FORMAT_INSTRUCTION_FOR_NO_IMAGE_GEN_SIMPLIFY,OUTPUT_FORMAT_INSTRUCTION_FOR_IMAGE_GEN_SIMPLIFY, simplify_agent_prompt_without_image,prompt_generator_prompt, general_sub_prompt_with_image_gen, general_sub_prompt_without_image_gen, topic_relevance_check_prompt, scaffolding_question_generation_prompt, answer_validation_prompt, scaffolding_response_prompt
from app.utils.retriever_tool import ContextRetrieverTool
from app.utils.simple_agent import run_graph as run_simple_agent_graph
from app.utils.socratic_agent import run_socratic_agent, stream_socratic_agent
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
from langchain_core.language_models import BaseChatModel
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
    print(f'LLM:   {name}')
    if name=="gpt-5":
        return ChatOpenAI(model = name, api_key = api_key, temperature = 1) 
    
    return ChatOpenAI(model = name,temperature = temperature, api_key = api_key) #

def load_llm_in_json_mode(api_key, name, temperature = 0.2):
    '''Load model and return'''
    print(f'LLM ---------------:   {name}')
    if name=="gpt-5":
        return ChatOpenAI(model = name, api_key = api_key, temperature = 1, model_kwargs={ "response_format": { "type": "json_object" } })
    return ChatOpenAI(model = name, api_key = api_key, temperature = temperature,  model_kwargs={ "response_format": { "type": "json_object" } })  #temperature = temperature,

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

async def topic_relevance_check_node(state):
    """Check if the current user question is related to the existing conversation topic"""
    print(f'topic relevance check started')
    
    # Create topic relevance checker
    topic_checker = _simple_prompt_assistant(llm=llm_for_langgraph, system_message=topic_relevance_check_prompt)
    
    # Get the current user question and conversation history
    current_question = state["messages"][-1].content if state["messages"] else ""
    conversation_history = state["messages"][:-1] if len(state["messages"]) > 1 else []
    
    # Create context for the topic checker
    context = {
        "user_input": [{"role": "user", "content": current_question}],
        "messages": conversation_history
    }
    
    response = await topic_checker.ainvoke(context)
    print(f'topic relevance response: {response.content}')
    
    try:
        # Parse the JSON response
        result = json.loads(response.content)
        is_topic_related = result.get("is_topic_related", True)  # Default to True if parsing fails
        
        print(f'topic relevance result: {is_topic_related}')
        return {"is_topic_related": is_topic_related}
        
    except json.JSONDecodeError as e:
        print(f'Error parsing topic relevance response: {e}')
        # Default to True if parsing fails
        return {"is_topic_related": True}

def intent_classifier_router(state) -> Literal["topic_relevance_check", "__end__"]:
    messages = state['messages']
    last_message = messages[-1]

    # Here if intent classifer clasify to be a clasify simplify flow then route to simply or if decide to move to socaratic flow then move to socaratic flow.
    if state["simlify"].lower() == 'yes':
      state['next_node'] = ''
      return '__end__'
    elif state["simlify"].lower() == 'no':
      state['next_node'] = 'topic_relevance_check'
      return 'topic_relevance_check'
    # Otherwise, we stop (send state to outliner)
    state['next_node'] = ''
    return '__end__'

def topic_relevance_router(state) -> Literal["prompter_node", "__end__"]:
    """Route based on topic relevance check"""
    
    # If topic is not related, force prompt regeneration
    if not state.get("is_topic_related", True):
        print(f'topic changed, forcing prompt regeneration')
        state['next_node'] = 'prompter_node'
        return 'prompter_node'
    
    # If topic is related, check if we need to regenerate prompt based on count
    current_count = state.get("count", 0)
    if current_count == 1 or current_count % 5 == 0:
        print(f'regular prompt regeneration triggered (count: {current_count})')
        state['next_node'] = 'prompter_node'
        return 'prompter_node'
    
    # No prompt regeneration needed
    print(f'no prompt regeneration needed (count: {current_count})')
    state['next_node'] = ''
    return '__end__'


async def prompter_node(state):
      print(f'inside prompter - generating new prompt based on topic relevance or count')
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
        count: Optional[int]
        is_topic_related: Optional[bool]

class ScaffoldingState(TypedDict):
    messages: Annotated[list, add_messages]
    scaffolding_level: str
    topic: str
    material: str
    scaffolding_questions: List[dict]
    answer_validation: dict
    response_content: str
    is_topic_related: Optional[bool]
    is_answer: Optional[bool]
        

llm_for_langgraph = load_llm(api_key=envs.OPENAI_API_KEY,name='gpt-4.1')
teacher_student_workflow = StateGraph(StudentTeacherState)

# Add nodes to the workflow
teacher_student_workflow.add_node("topic_relevance_check", topic_relevance_check_node)
teacher_student_workflow.add_node("prompter_node", prompter_node)

# Set entry point to topic relevance check
teacher_student_workflow.set_entry_point("topic_relevance_check")

# Add conditional edges
teacher_student_workflow.add_conditional_edges(
    "topic_relevance_check",
    topic_relevance_router
)

teacher_Student_graph = teacher_student_workflow.compile()#checkpointer=checkpointer


async def socaratic_agent_prompt_generation_flow(message_history,already_exiting_prompt, enable_image_generation,user_question,thread_id,counter, is_simplify):
    print(f'graph started')
      
    if is_simplify:
        print(f'is_simplify called for student.')
        if enable_image_generation:
            return simplify_agent_prompt_with_image + "\n" + OUTPUT_FORMAT_INSTRUCTION_FOR_IMAGE_GEN_SIMPLIFY, simplify_agent_prompt_with_image, 4 #when simlify prompt gets generated I must allow the promt creation that why 5
        else:
            return simplify_agent_prompt_without_image + "\n" +  OUTPUT_FORMAT_INSTRUCTION_FOR_NO_IMAGE_GEN_SIMPLIFY, simplify_agent_prompt_without_image, 4
        
    config = {"configurable": {"thread_id": f"{thread_id}"}}
    results = await teacher_Student_graph.ainvoke(
        {"messages": message_history + [("user", user_question)],
         "prompt": already_exiting_prompt,
         "count": counter}, 
        config
        )
    print(f'graph executed')
    print(f'state of graph is {results}')
    
    # Check if topic relevance triggered prompt regeneration
    if "prompt" in results:
        print(f'prompt regenerated - topic relevance: {results.get("is_topic_related", "unknown")}')
        if enable_image_generation:
            print(f'inside image generation prompt')
            return results["prompt"] + "\n" + general_sub_prompt_with_image_gen.strip(), results["prompt"], results["count"]
        print(f'inside prompt out of image gen')
        return results["prompt"] + "\n" + general_sub_prompt_without_image_gen.strip(), results["prompt"], results["count"]
    else:
        print(f'no prompt modification - topic still relevant')
        return already_exiting_prompt, already_exiting_prompt, results["count"]
    
    

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

def _create_custom_parser(schema_model):
    return JsonOutputParser(pydantic_object=schema_model) 


def _extract_image_from_chat_history(messages):
    """
    Look through chat messages to find generated image URL
    Searches backwards from latest message to find tool responses with image URLs
    """
    try:
        for message in reversed(messages):
            # Check if this is a tool message with image content
            if hasattr(message, 'content') and message.content:
                try:
                    # Try to parse as JSON (tool responses are often JSON)
                    import json
                    if isinstance(message.content, str):
                        content_data = json.loads(message.content)
                        # Look for image_url in the tool response
                        if isinstance(content_data, dict) and 'image_url' in content_data:
                            image_url = content_data['image_url']
                            if image_url and image_url != "null" and image_url != "None":
                                return image_url
                except (json.JSONDecodeError, TypeError):
                    # Not JSON, continue searching
                    continue
            
            # Check tool calls for image generation
            if hasattr(message, 'tool_calls') and message.tool_calls:
                for tool_call in message.tool_calls:
                    tool_name = tool_call.get('name', '').lower()
                    if 'image' in tool_name or 'generate' in tool_name:
                        # Image generation was called, continue looking for response
                        continue
        
        return None
    except Exception as e:
        print(f"Error extracting image from chat history: {e}")
        return None

        
async def get_stream_ai_response(LLM_ROLE, 
        memory_status, 
        llm, 
        chatbot_id: int, 
        org_id: int,  
        enable_image_generation=False, 
        chatbot_type = None, 
        chat_history = None, 
        thread_id = None,
        is_simplify = False,
        scaffolding_level: ScaffoldingLevel = ScaffoldingLevel.medium
        ):
    try:
        # Prepare messages for agents
        messages = chat_history
        ai_response_content = ""
        # Simple routing: Student vs Everyone else
        if chatbot_type == BotType.student:
            print('→ Using Socratic agent for student')
            async for chunk in stream_socratic_agent(
                thread_id=thread_id,
                messages=messages,
                chatbot_id=chatbot_id,
                org_id=org_id,
                llm=llm,
                personality=LLM_ROLE,
                scaffolding_level=scaffolding_level,
                memory_enabled=memory_status
            ):
            
                # Yield all chunks to frontend (including status messages)
                yield chunk
                
        else:
            print('→ Using Simple agent for regular chatbot')
            async for chunk in stream_simple_agent_graph(
                thread_id=thread_id,
                messages=messages,
                chatbot_id=chatbot_id,
                org_id=org_id,
                llm=llm,
                personality=LLM_ROLE,
                enable_image_generation=enable_image_generation,
                memory_enabled=memory_status
            ):
            
                # Yield all chunks to frontend (including status messages)
                yield chunk
    except Exception as e:
        logger.error(f"Error in streaming chat for thread {thread_id}: {e}")
        yield {"error": f"Error: {str(e)}", "type": "error"}
    


async def get_ai_response(
        LLM_ROLE, 
        memory_status, 
        llm, 
        chatbot_id: int, 
        org_id: int, 
        enable_image_generation=False, 
        chatbot_type = None, 
        chat_history = None, 
        thread_id = None,
        is_simplify = False,
        scaffolding_level: ScaffoldingLevel = ScaffoldingLevel.medium):
    """
    Simple routing between Socratic agent (students) and Simple agent (everyone else)
    """
    try:
        print(f'Routing chatbot_type: {chatbot_type}')
        
        # Prepare messages for agents
        messages = chat_history

        # Simple routing: Student vs Everyone else
        if chatbot_type == BotType.student:
            print('→ Using Socratic agent for student')
            agent_response = await run_socratic_agent(
                thread_id=thread_id,
                messages=messages,
                chatbot_id=chatbot_id,
                org_id=org_id,
                llm=llm,
                personality=LLM_ROLE,
                scaffolding_level=scaffolding_level,
                memory_enabled=memory_status
            )
        else:
            print('→ Using Simple agent for regular chatbot')
            agent_response = await run_simple_agent_graph(
                thread_id=thread_id,
                messages=messages,
                chatbot_id=chatbot_id,
                org_id=org_id,
                llm=llm,
                personality=LLM_ROLE,
                enable_image_generation=enable_image_generation,
                memory_enabled=memory_status
            )
        
        # Extract response content
        if not agent_response or "messages" not in agent_response:
            return {"answer": "I encountered an issue processing your request.", "image_url": None}
        
        response_messages = agent_response["messages"]
        if not response_messages:
            return {"answer": "I encountered an issue processing your request.", "image_url": None}
            
        # Get the last AI message content
        last_ai_content = None
        for message in reversed(response_messages):
            if hasattr(message, 'content') and message.content:
                last_ai_content = message.content
                break
                
        if not last_ai_content:
            return {"answer": "I encountered an issue processing your request.", "image_url": None}
        
        # Check for generated image in chat history (only if image generation is enabled)
        image_url = None
        if enable_image_generation and chatbot_type != BotType.student:
            image_url = _extract_image_from_chat_history(response_messages)
        
        return {
            "answer": last_ai_content,
            "image_url": image_url
        }

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
async def _add_message_database(thread_id, message_uuid,role, message, db_session, is_image = None, images_urls = None, organization_id = 0, is_simplify_on = False):
    try:   
        if is_image:
            message_data = Messages(
                thread_id = thread_id,
                organization_admin_id = organization_id,
                role = role,
                message_uuid = message_uuid,
                message_content = message,
                is_image = True,
                images_urls = images_urls,
                is_simplify_on = is_simplify_on)
            db_session.add(message_data)
        else:
            message_data = Messages(
                thread_id = thread_id,
                organization_admin_id = organization_id,
                message_uuid = message_uuid,
                role = role,
                message_content = message,
                is_simplify_on = is_simplify_on)
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

    print(f'skip == {skip} and limit ={limit}')
    if skip == 0 and limit == 0:
        result =  ( select(Messages)
            .filter(Messages.thread_id == thread_id)
            .order_by(desc(Messages.created_timestamp))  # Fetch latest messages first
            .offset(0) )
    else:
        result =  (select(Messages)
            .filter(Messages.thread_id == thread_id)
            .order_by(asc(Messages.created_timestamp))
            .offset(skip) 
            .limit(limit))
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
        if message.is_simplify_on:
            if message.role == 'Assistant':
                # chat_messages[-1].message = message.message_content
                chat_entry = Chats(
                role='ai',
                message= message.message_content,
                images_urls=message.images_urls if message.images_urls else None,
                message_id = message.message_uuid,
                is_revise = message.is_revised
                )
                chat_messages.append(chat_entry)
            continue
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
    


    