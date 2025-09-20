from app.utils.retriever_tool import ContextRetrieverTool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from app.common.env_config import get_envs_setting
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage, SystemMessage
from typing import Annotated, Literal
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
import logging
from app.utils.socratic_agent_prompts import SOCRATIC_GUIDANCE_SYSTEM_PROMPT, SCAFFOLDING_LEVELS

logger = logging.getLogger(__name__)
envs = get_envs_setting()

def create_tool_powered_agent(llm, system_message: str, tools=None):
    """Create an agent with tools."""
    prompt = ChatPromptTemplate.from_messages([
        ("system", "{system_message}"),
        MessagesPlaceholder(variable_name="messages"),
    ])
    prompt = prompt.partial(system_message=system_message)
    if tools:
        return prompt | llm.bind_tools(tools)
    else:
        return prompt | llm

def format_messages(messages: list[BaseMessage]):
    """Format messages for logging."""
    divider = "\n--------------------------------\n"
    res = f"{divider}"
    for message in messages:
        if isinstance(message, SystemMessage):
            res += f"System: {message.content}{divider}"
        elif isinstance(message, HumanMessage):
            content = message.content
            if isinstance(content, list):
                text_content = content[0].get('text', 'No text content') if content else 'No content'
            else:
                text_content = content
            res += f"Student: {text_content}{divider}"
        elif isinstance(message, AIMessage):
            if message.content:
                res += f"Socratic Agent: {message.content}{divider}"
            else:
                tool_calls = [f"Tool: {tool.get('name', 'Unknown')} | Args: {tool.get('args', 'No args')}"
                             for tool in message.tool_calls]
                res += f"Agent Called Tools: {', '.join(tool_calls)}{divider}"
        elif isinstance(message, ToolMessage):
            res += f"Tool Response: {message.content[:200]}...{divider}"
    return res

async def socratic_guidance_node(state):
    """Main Socratic guidance node that handles student interactions."""
    logger.info(f"Socratic guidance node start | Latest message: {format_messages([state['messages'][-1]] if state['messages'] else [])}")
    scaffolding_level = state.get("scaffolding_level", "medium")
    chatbot_id = state.get("chatbot_id")
    org_id = state.get("org_id")
    llm = state.get("llm")
    personality = state.get("personality")
    memory_enabled = state.get("memory_enabled", False)
    
    # Create tools for knowledge retrieval (student agents only use context retrieval)
    tools = [ContextRetrieverTool(chatbot_id=chatbot_id, org_id=org_id, memory_enabled=memory_enabled)]
    
    # Build system message with scaffolding level
    scaffolding_instructions = SCAFFOLDING_LEVELS.get(scaffolding_level, SCAFFOLDING_LEVELS["medium"])
    system_message = SOCRATIC_GUIDANCE_SYSTEM_PROMPT.format(
        scaffolding_level=scaffolding_level,
        scaffolding_instructions=scaffolding_instructions,
        personality=personality
    )
    
    # Create the agent
    socratic_agent = create_tool_powered_agent(
        llm=llm,
        tools=tools,
        system_message=system_message
    )
    
    # Generate response
    response = await socratic_agent.ainvoke({"messages": state["messages"]})
    logger.info(f"Socratic guidance node end | Response: {format_messages([response])}")
    return {"messages": response}

class SocraticAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    scaffolding_level: Annotated[str, "The scaffolding level: high, medium, or low"]
    chatbot_id: Annotated[int, "The id of the chatbot"]
    org_id: Annotated[int, "The id of the organization"]
    llm: Annotated[object, "The language model instance"]
    personality: Annotated[str, "The personality of the chatbot"]
    memory_enabled: Annotated[bool, "Whether memory is enabled"]

async def execute_rag_tools(state):
    """Execute tools for knowledge retrieval (student agents only use context retrieval)."""
    logger.debug(f"ToolNode received: {state['messages'][-1].tool_calls}")
    chatbot_id = state.get("chatbot_id")
    org_id = state.get("org_id")
    memory_enabled = state.get("memory_enabled", False)
    
    # Student agents only use context retrieval tool
    tools = [ContextRetrieverTool(chatbot_id=chatbot_id, org_id=org_id, memory_enabled=memory_enabled)]
    
    tool_executor = ToolNode(tools)
    output = await tool_executor.ainvoke(state)
    logger.debug(f"ToolNode output: {output}")
    return output

async def create_socratic_graph():
    """Create the Socratic teaching agent graph."""
    socratic_workflow = StateGraph(SocraticAgentState)
    # Add nodes
    socratic_workflow.add_node("socratic_guidance", socratic_guidance_node)
    socratic_workflow.add_node("rag_tools", execute_rag_tools)
    # Add edges
    socratic_workflow.add_edge(START, "socratic_guidance")
    # Conditional routing based on tool calls
    socratic_workflow.add_conditional_edges(
        "socratic_guidance",
        tools_condition,
        {
            "tools": "rag_tools",
            "__end__": END,
        },
    )
    # Return to guidance after tool execution
    socratic_workflow.add_edge("rag_tools", "socratic_guidance")
    # Compile the graph
    socratic_graph = socratic_workflow.compile()
    return socratic_graph

# Streaming functions removed - only non-streaming socratic agent supported
from app.models.chatbot_model import ScaffoldingLevel
async def run_socratic_agent(
    thread_id: str,
    messages: list[BaseMessage],
    chatbot_id: int,
    org_id: int,
    llm,
    personality: str,
    scaffolding_level: ScaffoldingLevel = ScaffoldingLevel.medium,
    memory_enabled: bool = False
) -> dict:
    """
    Run the Socratic teaching agent and return the response.
    Args:
        thread_id: Unique identifier for the conversation thread
        messages: List of conversation messages
        chatbot_id: The id of the chatbot
        org_id: The id of the organization
        llm: The language model instance
        scaffolding_level: Level of scaffolding (high, medium, low)
    Returns:
        Dict with the agent's response
    """
    try:
        # Create the graph
        socratic_graph = await create_socratic_graph()
        # Set up configuration
        config = {"configurable": {"thread_id": f"socratic_{thread_id}"}}
        # Initialize state
        initial_state = {
            "messages": messages,
            "scaffolding_level": scaffolding_level.value,
            "chatbot_id": chatbot_id,
            "org_id": org_id,
            "llm": llm,
            "personality": personality,
            "memory_enabled": memory_enabled,
        }
        # Run the graph
        response = await socratic_graph.ainvoke(initial_state, config)
        logger.info(f"Socratic agent response: {format_messages(response.get('messages', []))}")
        return response
        
    except Exception as e:
        logger.error(f"Error in run_socratic_agent: {e}")
        raise e

from typing import AsyncGenerator, Dict, Any
async def stream_socratic_agent(
    thread_id: str,
    messages: list[BaseMessage],
    chatbot_id: int,
    org_id: int,
    llm,
    personality: str,
    scaffolding_level: ScaffoldingLevel = ScaffoldingLevel.medium,
    memory_enabled: bool = False
) -> AsyncGenerator[str, None]:
    """
    Stream responses from the graph execution.
    Yields clear status updates and AI message content as it becomes available.
    """
    try:
        # Create the graph
        socratic_graph = await create_socratic_graph()
        
        config = {"configurable": {"thread_id": f"socratic_{thread_id}"}}
        # Initialize state
        initial_state = {
            "messages": messages,
            "scaffolding_level": scaffolding_level.value,
            "chatbot_id": chatbot_id,
            "org_id": org_id,
            "llm": llm,
            "personality": personality,
            "memory_enabled": memory_enabled,
        }
        # Stream the graph execution
        async for (message_chunk, metadata) in socratic_graph.astream(
            initial_state, 
            config,
            stream_mode="messages"
        ):
            # Handle different types of chunks
            if isinstance(message_chunk, AIMessageChunk):
                if message_chunk.content:
                    # Stream the actual AI response content
                    yield {"content": message_chunk.content, "type": "content"}
                elif message_chunk.tool_calls:
                    for tool_call in message_chunk.tool_calls:
                        tool_name = tool_call.get('name', 'Unknown Tool')
                        yield {"content": tool_name, "type": "tool_start"}

            elif isinstance(message_chunk, ToolMessage):
                tool_name = getattr(message_chunk, 'name', 'Tool')
                yield {"content": tool_name, "type": "tool_complete"}
                        
    except Exception as e:
        logger.error(f"Error in stream_graph_response: {e}")
        error_message = {"error": f"Error: {str(e)}", "type": "error"}
        yield error_message

