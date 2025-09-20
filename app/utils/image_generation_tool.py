import openai
from typing import Any, Dict
from pydantic import BaseModel
from langchain_core.tools import BaseTool
from app.common.env_config import get_envs_setting

envs = get_envs_setting()

class ImageGenerationToolInput(BaseModel):
    image_prompt: str

class ImageGenerationTool(BaseTool):
    """Tool for generating images using DALL-E"""
    
    name: str = "generate_image"
    description: str = "Generate an image based on the provided description/prompt. Use this tool when the user asks for image creation, drawing, or visual generation."
    args_schema: type[BaseModel] = ImageGenerationToolInput
    
    def __init__(self):
        super().__init__()
    
    async def _arun(self, image_prompt: str) -> Dict[str, Any]:
        """Generate an image using DALL-E"""
        try:
            print(f'Generating image with prompt: {image_prompt}')
            
            # Create OpenAI client and generate image
            client = openai.OpenAI(api_key=envs.OPENAI_API_KEY)
            response = client.images.generate(
                model="dall-e-3", 
                prompt=image_prompt,
                n=1,
                size='1024x1024'
            )
            
            image_url = response.data[0].url
            print(f'Image generated successfully: {image_url}')
            
            return {
                "image_url": image_url,
                "status": "success",
                "message": f"Successfully generated image: {image_prompt}"
            }
            
        except Exception as e:
            print(f"Error generating image: {e}")
            return {
                "image_url": None,
                "status": "error", 
                "message": f"Failed to generate image: {str(e)}"
            }
    
    def _run(self, image_prompt: str) -> Dict[str, Any]:
        """Synchronous version - not implemented"""
        raise NotImplementedError("This tool only supports async execution")
