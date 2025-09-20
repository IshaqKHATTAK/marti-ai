from app.utils.retriever_tool import ContextRetrieverTool
from app.utils.image_generation_tool import ImageGenerationTool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from app.utils.simple_agent_prompts import SIMPLE_AGENT_SYSTEM_PROMPT, SIMPLE_AGENT_SYSTEM_PROMPT_WITH_IMAGE
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage, SystemMessage, AIMessageChunk
from typing import Annotated
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
import logging

logger = logging.getLogger(__name__)

def _tool_powered_agent(llm, system_message: str, tools  = None):
    """Create an agent."""
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "{system_message}",
            ),
            MessagesPlaceholder(variable_name="messages"),
        ]
    )
    prompt = prompt.partial(system_message=system_message)
    if tools:
      return prompt | llm.bind_tools(tools)
    else:
      return prompt | llm
    
def _format_messages(messages: list[BaseMessage]):
    """Print the messages in a formatted way."""
    divider = "\n--------------------------------\n"
    res = f"{divider}"

    for message in messages:
        if isinstance(message, SystemMessage):
            res += f"System: {message.content}{divider}"
        if isinstance(message, HumanMessage):
            if isinstance(message.content, list) and len(message.content) > 0:
                content = message.content[0].get('text', 'No text content') if isinstance(message.content[0], dict) else str(message.content[0])
            else:
                content = str(message.content)
            res += f"User: {content}{divider}"
        elif isinstance(message, AIMessage):
            if message.content:
                res += f"Assistant: {message.content}{divider}"
            else:
                tool_calls_and_args = [f"Tool: {tool.get('name', 'Unknown Tool')} | Args: {tool.get('args', 'No arguments')}" for tool in message.tool_calls]
                res += f"Assistant Called Tools: {', '.join(tool_calls_and_args)}{divider}"
        elif isinstance(message, ToolMessage):
            res += f"Tool Response: {message.content}{divider}"
        else:
            message_type = getattr(message, 'type', type(message).__name__)
            message_content = getattr(message, 'content', str(message))
            res += f"{message_type}: {message_content}{divider}"

    return res

async def think_and_react_node(state):
      logger.info(f"start of think_and_react_node | latest message: {_format_messages([state['messages'][-1]] if state['messages'] else [])}")

      # Always include context retrieval tool
      tools = [ContextRetrieverTool(chatbot_id=state["chatbot_id"], org_id=state["org_id"], memory_enabled=state.get("memory_enabled", False))]

      system_message = SIMPLE_AGENT_SYSTEM_PROMPT
      system_message = system_message.format(personality=state["personality"])
      
      # Conditionally add image generation tool and update system message
      if state.get("enable_image_generation", False):
          tools.append(ImageGenerationTool())
          logger.info("Image generation tool enabled for this agent")
          system_message = SIMPLE_AGENT_SYSTEM_PROMPT_WITH_IMAGE
      
      ai_agent = _tool_powered_agent(llm=state["llm"], tools=tools, system_message=system_message)
      response = await ai_agent.ainvoke({"messages": state["messages"]})
      
      logger.info(f"end of think_and_react_node | produced response:\n{_format_messages([response])}")
      
      return {"messages":response}


class AgentGraphState(TypedDict):
    messages: Annotated[list, add_messages]
    chatbot_id: Annotated[int, "The id of the chatbot"]
    org_id: Annotated[int, "The id of the organization"]
    llm: Annotated[object, "The language model instance"]
    personality: Annotated[str, "The personality of the chatbot"]
    memory_enabled: Annotated[bool, "Whether memory is enabled"]
    enable_image_generation: Annotated[bool, "Whether image generation is enabled"]

async def create_graph():
    """Create the state graph for the database agent."""

    # state graph
    database_agent_workflow = StateGraph(AgentGraphState)

    async def execute_tools(state):
        """Executor for the tools."""
        logger.debug(f"ToolNode received input: {state['messages'][-1].tool_calls}")
        
        # Build tools list dynamically based on state
        tools = [ContextRetrieverTool(chatbot_id=state["chatbot_id"], org_id=state["org_id"], memory_enabled=state.get("memory_enabled", False))]
        if state.get("enable_image_generation", False):
            tools.append(ImageGenerationTool())
        
        tool_executor = ToolNode(tools)
        
        output = await tool_executor.ainvoke(state)
        logger.debug(f"ToolNode produced output: {output}")
        return output

    # nodes
    database_agent_workflow.add_node("think_and_react_agent", think_and_react_node)
    database_agent_workflow.add_node("tools", execute_tools)

    # start edge
    database_agent_workflow.add_edge(START, "think_and_react_agent")

    # route to tools based on think_and_react_agent output
    database_agent_workflow.add_conditional_edges(
                "think_and_react_agent",
                tools_condition,
                {
                    "tools": "tools",
                    "__end__": END,
                },
            )
    
    # edge from tools to think_and_react_agent
    database_agent_workflow.add_edge("tools", "think_and_react_agent")

    # compile the graph
    database_agent_graph = database_agent_workflow.compile()
    
    return database_agent_graph


async def run_graph(
    thread_id: str, 
    messages: list[BaseMessage], 
    chatbot_id: int, 
    org_id: int,
    llm,
    personality: str,
    enable_image_generation: bool = False,
    memory_enabled: bool = False
) -> str:
    """
    Run the graph and return the response.
    """
    try:
        # Create the graph
        database_agent_graph = await create_graph()
        
        # Set up the config
        config = {"configurable": {"thread_id": f"{thread_id}"}}

        # Run the graph
        response = await database_agent_graph.ainvoke(
            {
                "messages": messages, 
                "chatbot_id": chatbot_id, 
                "org_id": org_id, 
                "llm": llm,     
                "personality": personality,
                "memory_enabled": memory_enabled,
                "enable_image_generation": enable_image_generation
            }, 
            config
        )
        logger.info(f"end of run_graph | produced response:\n{_format_messages(response.get('messages', []))}")
        return response

    except Exception as e:
        logger.error(f"Error in run_graph: {e}")
        raise e


async def stream_general_graph(
    thread_id: str, 
    messages: list[BaseMessage], 
    chatbot_id: int, 
    org_id: int,
    llm,
    personality: str,
    enable_image_generation: bool = False,
    memory_enabled: bool = False
) -> AsyncGenerator[str, None]:
    """
    Stream responses from the graph execution.
    Yields clear status updates and AI message content as it becomes available.
    """
    try:
        # Create the graph
        chatting_agent_graph = await create_graph()
        
        # Set up the config
        config = {"configurable": {"thread_id": f"{thread_id}"}}
        
        # Stream the graph execution
        async for (message_chunk, metadata) in chatting_agent_graph.astream(
            {"messages": messages,
             "chatbot_id": chatbot_id, 
             "org_id": org_id, 
             "llm": llm,     
             "personality": personality,
             "memory_enabled": memory_enabled,
             "enable_image_generation": enable_image_generation
            }, 
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
                        yield {"content": tool_name, "type": "tool_start","additional_kwargs":tool_call.additional_kwargs,"response_metadata":tool_call.response_metadata,"id":tool_call.id,"tool_calls":tool_call.tool_calls}

            elif isinstance(message_chunk, ToolMessage):
                tool_name = getattr(message_chunk, 'name', 'Tool')
                yield {"content": tool_name, "type": "tool_complete","additional_kwargs":tool_call.additional_kwargs,"response_metadata":tool_call.response_metadata,"id":tool_call.id,"tool_calls":tool_call.tool_calls}
                        
    except Exception as e:
        logger.error(f"Error in stream_graph_response: {e}")
        error_message = {"error": f"Error: {str(e)}", "type": "error"}
        yield error_message

