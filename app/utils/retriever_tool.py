import asyncio
from pinecone import Pinecone as PineconeClient
from langchain_core.output_parsers import JsonOutputParser
from langchain.embeddings import OpenAIEmbeddings
from app.common.env_config import get_envs_setting
from typing import List, Dict, Any
from pydantic import BaseModel
from langchain_core.tools import BaseTool

# Initialize environment and clients
envs = get_envs_setting()
pc = PineconeClient(api_key=envs.PINECONE_API_KEY)
embeddings = OpenAIEmbeddings(api_key=envs.OPENAI_API_KEY, model=envs.EMBEDDINGS_MODEL)
index = pc.Index(envs.PINECONE_KNOWLEDGE_BASE_INDEX)

class ContextRetrieverToolInput(BaseModel):
    user_query: str

class ContextRetrieverTool(BaseTool):
    """Simple, focused context retriever for chatbot knowledge sources"""
    
    name: str = "context_retriever_tool"
    description: str = "Retrieve context from the knowledge base based on the user's query."
    args_schema: type[BaseModel] = ContextRetrieverToolInput
    
    # Declare custom fields for Pydantic validation
    chatbot_id: int
    org_id: int
    memory_enabled: bool = False
    namespace: str = ""
    
    def __init__(self, chatbot_id: int, org_id: int, memory_enabled: bool = False):
        super().__init__(
            chatbot_id=chatbot_id,
            org_id=org_id,
            memory_enabled=memory_enabled,
            namespace=f'{org_id}-kb'
        )

    async def _retrieve_from_source(self, query_embedding: List[float], content_source: str, top_k: int = 25) -> List[Dict]:
        """Generic retrieval function for any content source"""
        try:
            raw_results = index.query(
                vector=query_embedding,
                namespace=self.namespace,
                top_k=top_k,
                include_metadata=True,
                filter={
                    "content_source": content_source,
                    "chatbot_id": self.chatbot_id
                }
            )
            return raw_results.get("matches", [])
        except Exception as e:
            print(f"Error retrieving {content_source}: {e}")
            return []
    
    async def _rerank_documents(self, documents: List[Dict], query: str, top_n: int = 3) -> List[Dict]:
        """Re-rank documents using Pinecone reranker"""
        if not documents:
            return []
        
        try:
            # Format documents for reranker
            formatted_docs = []
            for doc in documents:
                text = doc.get("metadata", {}).get("text", "")
                if text:
                    formatted_docs.append({
                        "id": doc.get("id", ""),
                        "text": text[:1800]  # Truncate for reranker token limit
                    })
            
            if not formatted_docs:
                return []
            
            # Rerank documents
            rerank_result = pc.inference.rerank(
                model="pinecone-rerank-v0",
                query=query,
                documents=formatted_docs,
                top_n=top_n,
                return_documents=True
            )
            
            return rerank_result.data
        except Exception as e:
            print(f"Error reranking documents: {e}")
            return []
    
    async def retrieve_all_contexts(self, user_query: str, top_n: int = 3) -> str:
        """
        Main method to retrieve and format all contexts concurrently
        
        Args:
            user_query: User's input query
            top_n: Number of top documents to return per source
            
        Returns:
            Formatted markdown string with all contexts
        """
        # Step 1: Embed the user query
        query_embedding = embeddings.embed_query(user_query)
        
        # Step 2: Retrieve from all sources concurrently
        tasks = [
            self._retrieve_from_source(query_embedding, "doc"),
            self._retrieve_from_source(query_embedding, "url"), 
            self._retrieve_from_source(query_embedding, "prompt"),
            self._retrieve_from_source(query_embedding, "qa_pair"),
        ]
        
        # Add memory task if enabled
        if self.memory_enabled:
            tasks.append(self._retrieve_from_source(query_embedding, "memory"))
        
        # Execute all retrievals concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Unpack results
        doc_results = results[0] if not isinstance(results[0], Exception) else []
        url_results = results[1] if not isinstance(results[1], Exception) else []
        prompt_results = results[2] if not isinstance(results[2], Exception) else []
        qa_results = results[3] if not isinstance(results[3], Exception) else []
        memory_results = results[4] if self.memory_enabled and len(results) > 4 and not isinstance(results[4], Exception) else []
        
        # Step 3: Re-rank and format each source concurrently
        async def empty_result():
            return []
            
        rerank_tasks = []
        
        # Combine docs, urls, and prompts for knowledge base section
        knowledge_docs = doc_results + url_results + prompt_results
        if knowledge_docs:
            rerank_tasks.append(self._rerank_documents(knowledge_docs, user_query, top_n))
        else:
            rerank_tasks.append(empty_result())
            
        if qa_results:
            rerank_tasks.append(self._rerank_documents(qa_results, user_query, top_n))
        else:
            rerank_tasks.append(empty_result())
            
        if memory_results:
            rerank_tasks.append(self._rerank_documents(memory_results, user_query, top_n))
        else:
            rerank_tasks.append(empty_result())
        
        # Execute reranking concurrently
        reranked_results = await asyncio.gather(*rerank_tasks, return_exceptions=True)
        
        knowledge_ranked = reranked_results[0] if not isinstance(reranked_results[0], Exception) else []
        qa_ranked = reranked_results[1] if not isinstance(reranked_results[1], Exception) else []
        memory_ranked = reranked_results[2] if not isinstance(reranked_results[2], Exception) else []
        
        # Step 4: Format as markdown
        return self._format_as_markdown(knowledge_ranked, qa_ranked, memory_ranked)
    
    def _format_as_markdown(self, knowledge_docs: List[Dict], qa_docs: List[Dict], memory_docs: List[Dict]) -> str:
        """Format retrieved documents as clean markdown"""
        
        sections = []
        
        # Knowledge Base Content Section
        if knowledge_docs:
            sections.append("## Knowledge Base Content")
            sections.append("")
            for i, doc in enumerate(knowledge_docs, 1):
                text = doc.get("document", {}).get("text", "").strip()
                if text:
                    sections.append(f"**Document {i}:**")
                    sections.append(text)
                    sections.append("")
        
        # Few Shot Examples (QA Pairs)
        if qa_docs:
            sections.append("## Few Shot Examples")
            sections.append("")
            for i, doc in enumerate(qa_docs, 1):
                text = doc.get("document", {}).get("text", "").strip()
                if text:
                    sections.append(f"**Q/A Pair {i}:**")
                    sections.append(text)
                    sections.append("")
        
        # Memory Context
        if memory_docs:
            sections.append("## Memory Context")
            sections.append("")
            for i, doc in enumerate(memory_docs, 1):
                text = doc.get("document", {}).get("text", "").strip()
                if text:
                    sections.append(f"**Memory {i}:**")
                    sections.append(text)
                    sections.append("")
        
        # If no content found
        if not sections:
            return "## No Relevant Content Found\n\nNo relevant information was found in the knowledge base for this query."
        
        return "\n".join(sections).strip()

    async def _arun(self, user_query: str = "") -> dict[str, Any]:
        """Async wrapper for context retrieval"""
        return await self.retrieve_all_contexts(user_query)

    def _run(self, user_query: str = "") -> Dict[str, Any]:
        """Synchronous version - not implemented"""
        raise NotImplementedError("This tool only supports async execution")